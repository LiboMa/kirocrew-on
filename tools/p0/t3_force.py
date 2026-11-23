"""T3: a volume detached while mounted and being written (stays busy), then force-detached; on the other host the
filesystem recovers (ext4 e2fsck -p, xfs log replay at mount) and files written before the write load are checked."""
import json
import sys
import time
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import ebs_bench as e

writer, reader, rounds, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
rows = []
for fs in ("ext4", "xfs"):
    vol = e.aws("ec2", "create-volume", "--availability-zone", "us-east-1a", "--size", "4", "--volume-type", "gp3", "--encrypted",
                "--tag-specifications", "ResourceType=volume,Tags=[{Key=kirocrew:p0,Value=1}]")[0]["VolumeId"]
    e.wait_volume(vol, "available")
    dev = e.device_of(vol)
    first = True
    for n in range(rounds):
        e.aws("ec2", "attach-volume", "--volume-id", vol, "--instance-id", writer, "--device", "/dev/sdg")
        e.wait_volume(vol, "attached")
        prep = e.mount_script(vol, fs=fs, make=first) + (
            f"; cd {e.MOUNT} && rm -rf probe busy && mkdir probe && for i in $(seq 1 40); do head -c 1048576 /dev/urandom > probe/$i; done"
            f" && sha256sum probe/* > /root/sums-{fs} && sync; "
            f"(nohup dd if=/dev/urandom of={e.MOUNT}/busy bs=1M count=3000 oflag=direct status=none > /dev/null 2>&1 &); sleep 2; echo started")
        e.ssm(writer, prep)
        first = False
        t = time.time()
        e.aws("ec2", "detach-volume", "--volume-id", vol)
        time.sleep(20)
        state = e.aws("ec2", "describe-volumes", "--volume-ids", vol)[0]["Volumes"][0]
        busy = [a["State"] for a in state.get("Attachments", [])]
        needed_force = state["State"] != "available"   # Nitro: a mounted volume may detach without staying busy
        if needed_force:
            e.aws("ec2", "detach-volume", "--volume-id", vol, "--force")
        e.wait_volume(vol, "available")
        forced = round(time.time() - t, 1)
        sums = e.ssm(writer, f"cat /root/sums-{fs}; pkill -f 'of={e.MOUNT}/busy' ; umount -l {e.MOUNT} 2>/dev/null; true")[0]
        e.aws("ec2", "attach-volume", "--volume-id", vol, "--instance-id", reader, "--device", "/dev/sdg")
        e.wait_volume(vol, "attached")
        check = ("set +e; while [ ! -e {d} ]; do sleep 0.2; done; s=$(date +%s.%N); "
                 + ("e2fsck -p {d} > /tmp/fsck.out 2>&1; r=$?; " if fs == "ext4" else "r=0; ")
                 + "mkdir -p /mnt/r; mount {d} /mnt/r; m=$?; f=$(date +%s.%N); cd /mnt/r && echo \"$SUMS\" | base64 -d | sha256sum -c --quiet >/tmp/sum.out 2>&1; c=$?; "
                 "cd /; umount /mnt/r; python3 -c \"import sys;print('{{\\\"fsck_rc\\\":%s,\\\"mount_rc\\\":%s,\\\"recover_s\\\":%.2f,\\\"sums_ok\\\":%s}}'%(sys.argv[1],sys.argv[2],float(sys.argv[4])-float(sys.argv[3]),'true' if sys.argv[5]=='0' else 'false'))\" $r $m $s $f $c").format(d=dev)
        import base64
        listed = "\n".join(line for line in sums.splitlines() if " probe/" in line) + "\n"
        text, _ = e.ssm(reader, "export SUMS=" + base64.b64encode(listed.encode()).decode() + "; " + check)
        row = {"fs": fs, "n": n, "busy_states_after_20s": busy, "needed_force": needed_force, "force_detach_s": forced, **json.loads(text.strip().splitlines()[-1])}
        rows.append(row)
        print(json.dumps(row), flush=True)
        e.aws("ec2", "detach-volume", "--volume-id", vol)
        e.wait_volume(vol, "available")
summary = {fs: {"rounds": len([r for r in rows if r["fs"] == fs]),
                "sums_ok": sum(r["sums_ok"] for r in rows if r["fs"] == fs),
                "busy_seen": sum(1 for r in rows if r["fs"] == fs and r["busy_states_after_20s"]),
                "needed_force": sum(1 for r in rows if r["fs"] == fs and r["needed_force"]),
                **e.summary([r for r in rows if r["fs"] == fs], ("force_detach_s", "recover_s"))} for fs in ("ext4", "xfs")}
open(out, "w").write(json.dumps({"rows": rows, "summary": summary}, indent=2))
print(json.dumps(summary, indent=2))
