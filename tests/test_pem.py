"""PEM text from a Worker host is validated before deploy.sh pastes it into a root script on the platform (D28)."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
PEM = ROOT / "deploy/aws/pem.py"


def check(kind, text):
    return subprocess.run(["python3", "-I", str(PEM), kind], input=text, capture_output=True, text=True)


class PemTests(unittest.TestCase):
    def test_a_real_certificate_request_passes_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            csr = subprocess.run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", "k.pem",
                                  "-subj", "/CN=w1"], cwd=root, check=True, capture_output=True, text=True).stdout
        result = check("CERTIFICATE REQUEST", csr + "\n\t")   # SSM output may add trailing whitespace
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, csr.strip() + "\n")

    def test_anything_that_could_escape_the_heredoc_is_refused(self):
        body = "-----BEGIN CERTIFICATE REQUEST-----\nMIIB\n-----END CERTIFICATE REQUEST-----"
        for text in (body.replace("MIIB", "MIIB\nPEM_END\ntouch /tmp/owned"), body.replace("MIIB", "MII'B"),
                     body + "\nrm -rf /", "", body.replace("REQUEST", "REQUEST X")):
            self.assertNotEqual(check("CERTIFICATE REQUEST", text).returncode, 0, text)
        self.assertNotEqual(check("PRIVATE KEY", body).returncode, 0)

    def test_deploy_validates_before_pasting_and_uses_a_delimiter_outside_base64(self):
        deploy = (ROOT / "deploy/aws/deploy.sh").read_text()
        self.assertIn('python3 -I deploy/aws/pem.py "CERTIFICATE REQUEST"', deploy)
        self.assertIn('python3 -I deploy/aws/pem.py CERTIFICATE', deploy)
        self.assertIn("<<'PEM_END'", deploy)
        self.assertNotIn("<<'PEM'\n", deploy)


if __name__ == "__main__":
    unittest.main()
