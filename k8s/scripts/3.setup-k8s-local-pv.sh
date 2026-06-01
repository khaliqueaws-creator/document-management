cat > auto-mount-and-create-pv.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

BASE_MOUNT="/mnt/k8s-data"
STORAGE_CLASS="local-storage"

echo "===== Available block devices ====="
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS

echo
echo "===== Detecting unmounted whole disks ====="

mapfile -t DISKS < <(
  lsblk -dn -o NAME,TYPE | awk '$2=="disk"{print $1}' | while read -r disk; do
    DEV="/dev/$disk"

    # Skip OS/root disk
    if lsblk -nr -o MOUNTPOINTS "$DEV" | grep -q '^/$'; then
      continue
    fi

    # Skip disks with mounted partitions
    if lsblk -nr -o MOUNTPOINTS "$DEV" | grep -q '/'; then
      continue
    fi

    echo "$disk"
  done
)

if [ "${#DISKS[@]}" -eq 0 ]; then
  echo "No unmounted extra disks found."
  exit 0
fi

echo "Found unmounted disks:"
printf ' - %s\n' "${DISKS[@]}"

dnf install -y parted xfsprogs util-linux

INDEX=1

for DISK_NAME in "${DISKS[@]}"; do
  DISK="/dev/${DISK_NAME}"
  PARTITION="${DISK}p1"
  MOUNT_POINT="${BASE_MOUNT}${INDEX}"
  PV_NAME="local-pv-${INDEX}"

  echo
  echo "===== Processing $DISK ====="

  umount "${PARTITION}" 2>/dev/null || true
  umount "${DISK}" 2>/dev/null || true

  echo "Creating partition..."
  parted -s "$DISK" mklabel gpt
  parted -s "$DISK" mkpart primary xfs 0% 100%

  sleep 2
  partprobe "$DISK" || true
  udevadm settle || true

  echo "Formatting $PARTITION..."
  mkfs.xfs -f "$PARTITION"

  echo "Creating mount point $MOUNT_POINT..."
  mkdir -p "$MOUNT_POINT"

  echo "Mounting..."
  mount "$PARTITION" "$MOUNT_POINT"

  UUID=$(blkid -s UUID -o value "$PARTITION")

  echo "Updating /etc/fstab..."
  cp /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d%H%M%S)"

  sed -i "\|${PARTITION}|d" /etc/fstab
  sed -i "\|UUID=${UUID}|d" /etc/fstab
  sed -i "\| ${MOUNT_POINT} |d" /etc/fstab

  echo "UUID=${UUID} ${MOUNT_POINT} xfs defaults 0 0" >> /etc/fstab

  echo "Creating Kubernetes PersistentVolume ${PV_NAME}..."

cat <<PVEOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: ${PV_NAME}
spec:
  capacity:
    storage: 1900Mi
  accessModes:
    - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ${STORAGE_CLASS}
  hostPath:
    path: ${MOUNT_POINT}
PVEOF

  INDEX=$((INDEX + 1))
done

echo
echo "===== Testing fstab ====="
mount -a

echo
echo "===== Final verification ====="
lsblk -f
df -h | grep k8s-data || true
mount | grep k8s-data || true

echo
echo "===== Kubernetes PVs ====="
kubectl get pv

echo
echo "DONE."
EOF

chmod +x auto-mount-and-create-pv.sh
./auto-mount-and-create-pv.sh