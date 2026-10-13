#!/bin/bash
# run 2 detached: the Ethernet service off for the whole run, then back on (method note: instead of the VM's Terminal)
cd ~/room
sudo networksetup -setnetworkserviceenabled Ethernet off
sleep 5
{ date; networksetup -listallnetworkservices; route -n get default 2>&1 | head -3; } > offline-net.txt 2>&1
bash clean-room.sh --pkg knaif-1.3.0-macos-arm64.pkg --offline --fixtures . --model knaif-qwen3-4b-v2-q4_k_m.gguf > clean-room.out 2>&1
echo $? > offline.rc
sudo networksetup -setnetworkserviceenabled Ethernet on
