```powershell
$SERVER_IP = "100.54.217.37"
$SERVER_USER = "root"

$LOCAL_DIR = "C:\Users\UFUserAdmin\document-management\k8s"
$REMOTE_DIR = "/opt/k8s"

Write-Host "===== Creating remote directory ====="

ssh "${SERVER_USER}@${SERVER_IP}" "rm -rf ${REMOTE_DIR} && mkdir -p ${REMOTE_DIR}"

Write-Host "===== Uploading all files and folders ====="

scp -r "${LOCAL_DIR}\*" "${SERVER_USER}@${SERVER_IP}:${REMOTE_DIR}/"

Write-Host "===== Fixing Linux line endings and permissions ====="

ssh "${SERVER_USER}@${SERVER_IP}" "find ${REMOTE_DIR} -type f -name '*.sh' -exec sed -i 's/\r$//' {} \; && find ${REMOTE_DIR} -type f -name '*.sh' -exec chmod +x {} \;"

Write-Host "===== Running master script ====="

ssh "${SERVER_USER}@${SERVER_IP}" "cd ${REMOTE_DIR}/scripts && bash ./run-all.sh"

Write-Host "===== COMPLETED ====="
```
