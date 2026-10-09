#!/bin/bash
# Does Installer stop a package script at pkgbuild's default timeout="600"? Stays alive 12 minutes.
start=$(date +%s)
echo "timeout-test: started"
for i in $(seq 1 24); do
  sleep 30
  echo "timeout-test: alive at $(( $(date +%s) - start ))s"
done
echo "timeout-test: finished after $(( $(date +%s) - start ))s"
exit 0
