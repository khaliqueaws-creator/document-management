#!/usr/bin/env bash
set -euo pipefail

K8S_VERSION="v1.30"
POD_CIDR="10.244.0.0/16"

echo "===== Rocky Linux 9 Kubernetes Single Node Lab Install ====="

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root"
  exit 1
fi

echo "===== OS Prep ====="
dnf update -y

swapoff -a || true
sed -i '/swap/d' /etc/fstab

setenforce 0 || true
sed -i 's/^SELINUX=enforcing$/SELINUX=permissive/' /etc/selinux/config || true

systemctl disable --now firewalld || true

cat <<EOF >/etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF

modprobe overlay
modprobe br_netfilter

cat <<EOF >/etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
EOF

sysctl --system

echo "===== Install tools ====="
dnf install -y curl wget vim bash-completion bind-utils openssl iproute-tc dnf-plugins-core

echo "===== Install containerd ====="
dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
dnf install -y containerd.io

mkdir -p /etc/containerd
containerd config default >/etc/containerd/config.toml
sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml

systemctl enable --now containerd
systemctl restart containerd

echo "===== Install Kubernetes ====="
cat <<EOF >/etc/yum.repos.d/kubernetes.repo
[kubernetes]
name=Kubernetes
baseurl=https://pkgs.k8s.io/core:/stable:/${K8S_VERSION}/rpm/
enabled=1
gpgcheck=1
gpgkey=https://pkgs.k8s.io/core:/stable:/${K8S_VERSION}/rpm/repodata/repomd.xml.key
EOF

dnf install -y kubelet kubeadm kubectl
systemctl enable --now kubelet

echo "===== Initialize Kubernetes ====="
kubeadm init --pod-network-cidr="${POD_CIDR}"

mkdir -p "$HOME/.kube"
cp -f /etc/kubernetes/admin.conf "$HOME/.kube/config"
chown "$(id -u):$(id -g)" "$HOME/.kube/config"

echo "===== Install Flannel ====="
kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml

echo "===== Allow pods on control-plane node ====="
kubectl taint nodes --all node-role.kubernetes.io/control-plane- || true
kubectl taint nodes --all node-role.kubernetes.io/master- || true

echo "===== Wait for cluster ====="
kubectl wait --for=condition=Ready node --all --timeout=300s
kubectl -n kube-system rollout status deployment/coredns --timeout=300s

echo "===== Validate DNS from pod ====="
kubectl run dns-test --rm -i --restart=Never --image=busybox:1.36 -- nslookup google.com



