"""T4: duplicate filesystem UUIDs (a snapshot restored twice onto one host), ext4 and xfs, and rewriting the UUID."""
import json
import sys
import time
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import ebs_bench as e

host, out = sys.argv[1], {}
for fs in ("ext4", "xfs"):
    src = e.aws("ec2", "create-volume", "--availability-zone", "us-east-1a", "--size", "2", "--volume-type", "gp3", "--encrypted",
                "--tag-specifications", "ResourceType=volume,Tags=[{Key=kirocrew:p0,Value=1}]")[0]["VolumeId"]
    e.wait_volume(src, "available")
    e.aws("ec2", "attach-volume", "--volume-id", src, "--instance-id", host, "--device", "/dev/sdf")
    e.wait_volume(src, "attached")
    e.ssm(host, e.mount_script(src, fs=fs, make=True) + f"; echo data > {e.MOUNT}/probe; sync; umount {e.MOUNT}")
    e.aws("ec2", "detach-volume", "--volume-id", src)
    e.wait_volume(src, "available")
    snap = e.aws("ec2", "create-snapshot", "--volume-id", src, "--tag-specifications",
                 "ResourceType=snapshot,Tags=[{Key=kirocrew:p0,Value=1}]")[0]["SnapshotId"]
    e.aws("ec2", "wait", "snapshot-completed", "--snapshot-ids", snap, check=False)
    copies = []
    for index in range(2):
        vol = e.aws("ec2", "create-volume", "--availability-zone", "us-east-1a", "--snapshot-id", snap, "--volume-type", "gp3",
                    "--tag-specifications", "ResourceType=volume,Tags=[{Key=kirocrew:p0,Value=1}]")[0]["VolumeId"]
        e.wait_volume(vol, "available")
        e.aws("ec2", "attach-volume", "--volume-id", vol, "--instance-id", host, "--device", e.device_name(5 + index))
        e.wait_volume(vol, "attached")
        copies.append(vol)
    a, b = (e.device_of(v) for v in copies)
    script = (f"set +e; mkdir -p /mnt/c1 /mnt/c2; for d in {a} {b}; do while [ ! -e $d ]; do sleep 0.2; done; done; "
              f"blkid -o value -s UUID {a} {b}; mount {a} /mnt/c1; echo first=$?; mount {b} /mnt/c2 2>&1 | tail -1; echo second=$?; "
              f"umount /mnt/c2 2>/dev/null; "
              + (f"mount -o nouuid {b} /mnt/c2; echo nouuid=$?; umount /mnt/c2; umount /mnt/c1; xfs_admin -U generate {b} >/dev/null; echo regen=$?; "
                 if fs == "xfs" else
                 f"umount /mnt/c1; e2fsck -fy {b} >/dev/null; tune2fs -U random {b} >/dev/null; echo regen=$?; ")
              + f"blkid -o value -s UUID {a} {b}; mount {a} /mnt/c1; mount {b} /mnt/c2; echo both_after_regen=$?; cat /mnt/c2/probe; "
              "umount /mnt/c1 /mnt/c2")
    text, _ = e.ssm(host, script)
    out[fs] = text.strip().splitlines()
    for vol in copies + [src]:
        e.aws("ec2", "detach-volume", "--volume-id", vol, check=False)
    for vol in copies:
        e.wait_volume(vol, "available")
print(json.dumps(out, indent=2, ensure_ascii=False))
