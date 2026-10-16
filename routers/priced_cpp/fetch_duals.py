#!/usr/bin/env python3
"""Fetch lpbound prices for a suite, checking Git hashes and instance identity."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request


def read(url):
    request = urllib.request.Request(url,headers={"User-Agent":"priced-cpp-router"})
    with urllib.request.urlopen(request,timeout=60) as response:
        return response.read()


def blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite",type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True)
    parser.add_argument("--remote-dir",required=True)
    parser.add_argument("--repo",default="lblommesteyn/eda-3d-routing-challenge")
    parser.add_argument("--ref",default="eb8fae4602b059318c61ea53d887c34c67594f3d")
    args = parser.parse_args()
    def directory(path):
        url = f"https://api.github.com/repos/{args.repo}/contents/{path}?ref={urllib.parse.quote(args.ref,safe='')}"
        return {entry["name"]:entry for entry in json.loads(read(url))}
    remote = directory(args.remote_dir)
    instances = directory(args.suite.name)
    manifest = json.loads((args.suite/"suite.json").read_text())
    args.out_dir.mkdir(parents=True,exist_ok=True)
    rows,bounds = [],{}
    for case in manifest["cases"]:
        name = case["name"]
        instance = args.suite/case["instance_file"]
        if blob(instance.read_bytes()) != instances[instance.name]["sha"]:
            raise ValueError(f"local instance differs from price source: {instance}")
        entry = remote[name+".alpha.npz"]
        target = args.out_dir/entry["name"]
        data = target.read_bytes() if target.exists() else read(entry["download_url"])
        if blob(data) != entry["sha"]:
            raise ValueError(f"dual file hash mismatch: {target}")
        target.write_bytes(data)
        verification = remote["verify_"+name+".json"]
        checked = read(verification["download_url"])
        if blob(checked) != verification["sha"]:
            raise ValueError(f"verification hash mismatch: {name}")
        bound = json.loads(checked)
        if bound["case"] != f"{args.suite.name}/{case['instance_file']}":
            raise ValueError(f"bound refers to another instance: {name}")
        bounds[name] = bound["certified_int_bound"]
        rows.append(dict(case=name,source=entry["html_url"],git_blob=entry["sha"],
                         sha256=hashlib.sha256(data).hexdigest(),instance_blob=instances[instance.name]["sha"],
                         verification=bound,verification_blob=verification["sha"]))
        print(f"{name}: verified {len(data)} bytes, bound={bounds[name]}",flush=True)
    (args.out_dir/"bounds.json").write_text(json.dumps(bounds,indent=2)+"\n")
    (args.out_dir/"PROVENANCE.json").write_text(json.dumps(dict(repository=args.repo,
        ref=args.ref,path=args.remote_dir,files=rows),indent=2)+"\n")


if __name__ == "__main__":
    main()
