"""A stand-in for the aws CLI that lets tests run deploy.sh without DEPLOY_DRY_RUN (tests/test_deploy_assets.py).

State (FAKE_AWS_STATE, JSON): {"stacks": {name: {"params": {...}, "outputs": {...}}}, "whoami": str,
"throttle": [stack name, or "*list*" for the stack listing]}. Every call is appended to FAKE_AWS_LOG; a change set's
parameters file is logged as "PARAMS <json>" and an SSM command as "COMMAND <script>".
"""
import json
import os
import re
import sys

args = sys.argv[1:]
state = json.load(open(os.environ["FAKE_AWS_STATE"]))
log = open(os.environ["FAKE_AWS_LOG"], "a")
log.write("CALL " + " ".join(args) + "\n")


def arg(flag):
    return args[args.index(flag) + 1] if flag in args else None


def fail(message):
    sys.stderr.write(message + "\n")
    sys.exit(255)


stacks, query = state.get("stacks", {}), arg("--query") or ""
if "describe-stacks" in args:
    name = arg("--stack-name")
    if name is None:
        if "*list*" in state.get("throttle", []):
            fail("An error occurred (Throttling) when calling the DescribeStacks operation: Rate exceeded")
        prefix = re.search(r"starts_with\(StackName, '([^']+)'\)", query).group(1)
        excluded = re.search(r"StackName != '([^']+)'", query)
        ports = [s["params"]["RelayPort"] for n, s in stacks.items() if n.startswith(prefix)
                 and (not excluded or n != excluded.group(1)) and "RelayPort" in s.get("params", {})]
        print("\t".join(ports))
        sys.exit(0)
    if query == "Stacks[0].Outputs":  # printed after a change set ran; the stack exists by then
        sys.exit(0)
    if name in state.get("throttle", []):
        fail("An error occurred (Throttling) when calling the DescribeStacks operation: Rate exceeded")
    if name not in stacks:
        fail(f"An error occurred (ValidationError) when calling the DescribeStacks operation: Stack with id {name} does not exist")
    stack = stacks[name]
    key = re.search(r"Key=='([^']+)'", query)
    if "Parameters" in query:
        print(stack.get("params", {}).get(key.group(1), "None"))
    elif "Outputs[?" in query:
        print(stack.get("outputs", {}).get(key.group(1), "None"))
    elif "StackStatus" in query:
        print("UPDATE_COMPLETE")
    sys.exit(0)
if "create-change-set" in args:
    log.write("PARAMS " + open(arg("--parameters")[len("file://"):]).read().strip() + "\n")
    print("{}")
elif "describe-change-set" in args:
    print("None")
elif "get-parameter" in args:
    print("o" * 48)
elif "send-command" in args:
    parameters = arg("--parameters")
    if parameters.startswith("file://"):
        parameters = open(parameters[len("file://"):]).read()
    command = json.loads(parameters)["commands"][0]
    log.write("COMMAND " + command.replace("\n", " ") + "\n")
    with open(os.environ["FAKE_AWS_LOG"] + ".last", "w") as last:
        last.write(command)
    print("cmd-fake")
elif "get-command-invocation" in args:
    last = open(os.environ["FAKE_AWS_LOG"] + ".last").read()
    if query == "Status":
        print("Success")
    elif query == "StandardOutputContent":
        print(state.get("whoami", "") if "kiro-cli whoami" in last else "")
log.close()
