"""MVP 0.2.0 P2 Task 7 (design §4.2.4, S29, S31): after the install the host role keeps Bedrock only, so every later AWS
change is the deployment kit's — `kirocrew-on single native | backup | up`, run on the administrator's computer or in
CloudShell with the administrator's credentials; what has to happen on the host goes through SSM, and no administrator
credential is left there. The platform only records what is waiting (aws_requests, no secret in it); the kit reports
back and the console stops showing it. A fake aws records every call."""
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
import urllib.parse

from octopus import single
from tests.test_access import AccessCase

KEY_ID, SECRET_KEY, SESSION = "AKIAADMINKEYEXAMPLE1", "admin/Secret+Key/ForTheKitOnly000000001", "session-token-of-the-admin"
FAKE_AWS = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
state_path = os.environ["FAKE_STATE"]
state = json.load(open(state_path))
log = open(os.environ["FAKE_LOG"], "a")
log.write("CALL " + " ".join(args) + "\n")
def out(value):
    print(json.dumps(value) if not isinstance(value, str) else value); sys.exit(0)
def arg(flag):
    return args[args.index(flag) + 1] if flag in args else None
if "export-credentials" in args:
    out({"Version": 1, "AccessKeyId": os.environ["KIT_KEY_ID"], "SecretAccessKey": os.environ["KIT_SECRET"],
         "SessionToken": os.environ["KIT_SESSION"]})
if "describe-stacks" in args:
    params = [{"ParameterKey": k, "ParameterValue": v} for k, v in state["params"].items()]
    outputs = [{"OutputKey": k, "OutputValue": v} for k, v in state["outputs"].items()]
    out({"Stacks": [{"StackName": arg("--stack-name"), "StackStatus": "UPDATE_COMPLETE", "Parameters": params, "Outputs": outputs}]})
if "create-change-set" in args:
    log.write("PARAMS " + open(arg("--parameters")[len("file://"):]).read().strip() + "\n")
    log.write("TEMPLATE " + arg("--template-body") + "\n")
    for p in json.load(open(arg("--parameters")[len("file://"):])):
        if "ParameterValue" in p:
            state["params"][p["ParameterKey"]] = p["ParameterValue"]
            if p["ParameterKey"].startswith("NativeRelay") and p["ParameterValue"]:
                state["outputs"]["NativeRelayDomain" + p["ParameterKey"][len("NativeRelay"):]] = "d9relay%s.cloudfront.net" % p["ParameterKey"][-1]
    json.dump(state, open(state_path, "w"))
    out({"Id": "cs-1"})
if "wait" in args: sys.exit(0)
if "describe-change-set" in args: out({"Status": "CREATE_COMPLETE", "Changes": state.get("changes", [])})
if "execute-change-set" in args or "delete-change-set" in args: out("")
if "send-command" in args:
    log.write("COMMAND " + json.loads(arg("--parameters"))["commands"][0].replace("\n", " ⏎ ") + "\n")
    out({"Command": {"CommandId": "cmd-1"}})
if "get-command-invocation" in args:
    out({"Status": state.get("ssm_status", "Success"), "StandardOutputContent": state.get("ssm_out", "ok\n"),
         "StandardErrorContent": state.get("ssm_err", "")})
sys.stderr.write("unexpected call: " + " ".join(args) + "\n"); sys.exit(99)
'''
PARAMS = {"InstanceId": "i-0abc123", "SecurityGroupId": "sg-1", "CloudFrontPrefixListId": "pl-1", "OriginSecret": "****",
          **{f"NativeRelay{i}": "" for i in range(1, 10)}}


class KitCase(unittest.TestCase):
    def setUp(self):
        self.kit_tmp = tempfile.TemporaryDirectory()   # AccessCase keeps its database in self.tmp
        self.addCleanup(self.kit_tmp.cleanup)
        self.dir = Path(self.kit_tmp.name)
        (self.dir / "bin").mkdir()
        (self.dir / "bin/aws").write_text(FAKE_AWS)
        (self.dir / "bin/aws").chmod(0o755)
        self.state = {"params": dict(PARAMS), "outputs": {"ConsoleDomain": "d1.cloudfront.net"}}
        self.log = self.dir / "aws.log"
        patcher = mock.patch.dict(os.environ, {"PATH": f"{self.dir / 'bin'}:{os.environ['PATH']}", "FAKE_LOG": str(self.log),
                                               "FAKE_STATE": str(self.dir / "state.json"), "KIT_KEY_ID": KEY_ID,
                                               "KIT_SECRET": SECRET_KEY, "KIT_SESSION": SESSION})
        patcher.start()
        self.addCleanup(patcher.stop)
        poll = mock.patch.object(single, "POLL", 0)
        poll.start()
        self.addCleanup(poll.stop)

    def kit(self, *argv):
        (self.dir / "state.json").write_text(json.dumps(self.state))
        return single.main(["--region", "us-east-1", *argv])

    def lines(self, prefix):
        return [line[len(prefix):] for line in self.log.read_text().splitlines() if line.startswith(prefix)]

    def parameters(self):
        return json.loads(self.lines("PARAMS ")[-1])


class NativeTests(KitCase):
    def test_a_boundary_gets_the_first_free_slot_and_the_host_relays_it(self):
        self.state["params"]["NativeRelay1"] = "carol"
        self.assertEqual(self.kit("native", "bob"), 0)
        params = self.parameters()
        self.assertIn({"ParameterKey": "NativeRelay2", "ParameterValue": "bob"}, params)
        for key in PARAMS:   # everything else, the origin secret included, stays the stack's own
            if key != "NativeRelay2":
                self.assertIn({"ParameterKey": key, "UsePreviousValue": True}, params)
        [command] = self.lines("COMMAND ")
        self.assertEqual(command, "bash /opt/kirocrew-on/current/deploy/install/boundary-native.sh bob 8921 d9relay2.cloudfront.net")
        self.assertTrue(self.lines("TEMPLATE ")[0].endswith("deploy/aws/kirocrew-on-single.yaml"))

    def test_a_boundary_that_has_its_slot_keeps_it(self):
        self.state["params"]["NativeRelay4"] = "bob"
        self.kit("native", "bob")
        self.assertIn({"ParameterKey": "NativeRelay4", "ParameterValue": "bob"}, self.parameters())
        self.assertIn(" bob 8923 ", self.lines("COMMAND ")[0])

    def test_nine_slots_at_most_and_plain_names_only(self):
        self.state["params"].update({f"NativeRelay{i}": f"user{i}" for i in range(1, 10)})
        with self.assertRaises(SystemExit) as stop:
            self.kit("native", "bob")
        self.assertIn("9", str(stop.exception))
        for name in ("personal", "Bob", "x;reboot"):
            with self.subTest(name), self.assertRaises(SystemExit):
                self.kit("native", name)

    def test_a_change_that_replaces_something_is_refused(self):
        self.state["changes"] = [{"ResourceChange": {"LogicalResourceId": "HostAddress", "Replacement": "True"}}]
        with self.assertRaises(SystemExit) as stop:
            self.kit("native", "bob")
        self.assertIn("HostAddress", str(stop.exception))
        self.assertEqual(self.lines("COMMAND "), [])


class BackupTests(KitCase):
    def test_the_host_uploads_one_backup_to_one_object_through_a_short_lived_url(self):
        self.state["ssm_out"] = "123456\n"
        self.assertEqual(self.kit("backup", "--bucket", "my-backups"), 0)
        [command] = self.lines("COMMAND ")
        # review 2026-10-28: SSM runs commands with sh (dash on Ubuntu): only a heredoc into the release's bash script
        self.assertTrue(command.startswith("bash /opt/kirocrew-on/current/deploy/install/backup-upload.sh <<'KC_URL' ⏎ "), command)
        self.assertNotIn("pipefail", command)
        url = re.search(r"(https://\S+)", command).group(1)
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        self.assertEqual(parts.netloc, "my-backups.s3.us-east-1.amazonaws.com")
        self.assertRegex(parts.path, r"^/kirocrew-on/backups/i-0abc123/\d{8}T\d{6}Z\.tar\.gz$")
        self.assertLessEqual(int(query["X-Amz-Expires"]), 900)
        self.assertEqual(query["X-Amz-SignedHeaders"], "host")
        self.assertTrue(query["X-Amz-Credential"].startswith(KEY_ID + "/"))
        script = (Path(single.__file__).parents[1] / "deploy/install/backup-upload.sh").read_text()
        self.assertIn("runuser -u octopus -- /usr/local/bin/kirocrew-on --config /etc/kirocrew-on/platform.toml backup --dest", script)
        self.assertIn("curl -fsS -K -", script)   # the URL reaches curl on its standard input, never its argv
        for secret in (SECRET_KEY, urllib.parse.quote(SECRET_KEY, safe="")):   # no admin credential reaches the host
            self.assertNotIn(secret, command)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", command)

    def test_presigning_matches_awss_published_example(self):
        # docs.aws.amazon.com/AmazonS3/latest/API/sigv4-query-string-auth.html (GET, 2013-05-24, 86400 s)
        url = single.presign("GET", "examplebucket", "test.txt", "us-east-1",
                             {"AccessKeyId": "AKIAIOSFODNN7EXAMPLE", "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"},
                             86400, now="20130524T000000Z", host="examplebucket.s3.amazonaws.com")
        self.assertTrue(url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"), url)


class UpTests(KitCase):
    def test_the_stack_takes_the_releases_template_with_every_value_it_has(self):
        self.assertEqual(self.kit("up"), 0)
        self.assertEqual(self.parameters(), [{"ParameterKey": key, "UsePreviousValue": True} for key in PARAMS])
        self.assertEqual(self.lines("COMMAND "), [])   # nothing to do on the host

    def test_it_is_a_kirocrew_on_command(self):
        from octopus.__main__ import main
        (self.dir / "state.json").write_text(json.dumps(self.state))
        self.assertEqual(main(["single", "--region", "us-east-1", "up"]), 0)


class PendingTests(AccessCase, KitCase):
    def setUp(self):
        AccessCase.setUp(self)
        KitCase.setUp(self)

    def tearDown(self):
        AccessCase.tearDown(self)

    def test_waiting_changes_carry_no_secret_and_leave_the_list_once_the_kit_reports(self):
        with self.http() as call:
            status, made = call("POST", "/v1/admin/aws-requests", self.tokens["alice-console"], {"kind": "native", "params": {"boundary": "bob"}})
            self.assertEqual(status, 201, made)
            self.assertIn(f"kirocrew-on single native bob --request {made['id']}", made["command"])
            for body in ({"kind": "native", "params": {"boundary": "x; reboot"}}, {"kind": "backup", "params": {"bucket": "A_B"}},
                         {"kind": "shell", "params": {}}, {"kind": "native", "params": {"boundary": "bob", "secret": "x"}}):
                with self.subTest(body):
                    self.assertEqual(call("POST", "/v1/admin/aws-requests", self.tokens["alice-console"], body)[0], 400)
            self.assertEqual(call("POST", "/v1/admin/aws-requests", self.tokens["bob"], {"kind": "up", "params": {}})[0], 403)
            role = Path(self.dir / "host-role.json")
            role.write_text(json.dumps({"state": "writes", "role": "kirocrew-on-host", "allowed": ["ec2:RunInstances"],
                                        "commands": ["aws iam detach-role-policy --role-name kirocrew-on-host --policy-arn x"],
                                        "checked": 1.0}))
            with mock.patch("octopus.domain.Service.HOST_ROLE", role):   # install.sh's check-role, shown until fixed
                answer = call("GET", "/v1/admin/aws-requests", self.tokens["alice-console"])[1]
            self.assertEqual((answer["host_role"]["state"], answer["host_role"]["allowed"]), ("writes", ["ec2:RunInstances"]))
            with mock.patch("octopus.domain.Service.HOST_ROLE", self.dir / "absent.json"):
                self.assertIsNone(call("GET", "/v1/admin/aws-requests", self.tokens["alice-console"])[1]["host_role"])
            listed = call("GET", "/v1/admin/aws-requests", self.tokens["alice-console"])[1]["items"]
            self.assertEqual([(i["id"], i["state"]) for i in listed], [(made["id"], "pending")])
            token = self.dir / "token"
            token.write_text(self.tokens["alice"])   # the kit uses an administrator's API token
            self.kit("native", "bob", "--platform", call.base, "--token-file", str(token), "--request", made["id"])
            [item] = call("GET", "/v1/admin/aws-requests", self.tokens["alice-console"])[1]["items"]
            self.assertEqual((item["state"], item["result"]["native_domain"]), ("done", "d9relay1.cloudfront.net"))


if __name__ == "__main__":
    unittest.main()
