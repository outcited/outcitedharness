import json,sys
for d in json.load(sys.stdin):
    r=d.get("running","false")
    if r=="true" or d.get("comment"):
        print("%-16s run=%-5s %-9s %s" % (d.get("name","?"), r, d.get("speed","-"), d.get("comment","")))
