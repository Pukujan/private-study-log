(main) root@C.39989741:/workspace$ for pid in $(pgrep caddy); do echo "PID=$pid"; sudo tr '\0' '\n' < /proc/$pid/environ | grep LOCAL_LLM_API_KEY; done
PID=518
PID=1021
PID=8349
LOCAL_LLM_API_KEY=67f11ac71387ef2c087b1ee4f1ea16f5334b87214abf344c0a68b694e710172f
(main) root@C.39989741:/workspace$ 
