# Ubuntu migration preflight — 2026-11-18

Canonical checkout /Users/zap/Documents/netguard. The parent directory is outside Git.
Initial git status --short produced no output. Branch netguard-p0 at af9181639652f69332365ce855d0f63481df71ba. Local main and MIT upstream point to faa913a6f1a99d25b2c3b0cabd1986ef3d90356c. Origin is https://github.com/giaphoang/netguard.git and upstream is https://github.com/EricaNordlof/network-platform-lab.git.

Verified complete Git bundle and readable 67-file tracked-source tarball in /Users/zap/Documents/NetGuard-preservation-Ei9rXC. Only versioned files and reachable Git history were archived. The working tree was clean.

macOS 26.5 (25F71), arm64, 18 GiB RAM, 11 logical CPUs. Initial available storage 114829892 KiB (109.51 GiB). Docker Desktop running, context desktop-linux. Planned allocation 4 vCPUs, 6 GiB RAM (one third of host memory), and one sparse 32 GiB disk.

fabric.yml declares four FRR 10.1.1 routers, two BusyBox hosts, and six internal parentless macvlan segments. The separate docker-compose.yml contains the upstream stack. Existing leaf and spine configs live under lab/. The hosts use explicit routes to the opposite access subnet via their local leaf. scripts/smoke.py and scripts/verify_failover.py check real routes and bidirectional probes. The latter writes scenarios/failover.json and requires separate evidence handling during migration to preserve the original fixture.

docs/proof/P0.md records failed probes on internal Desktop bridges. docs/proof/failover-verification.txt records subsequent success with macvlan, including alternate-path forwarding and restoration. These are historical Desktop observations. Ubuntu acceptance requires fresh guest evidence.

UTM 5.0.9 (Homebrew cask 5.0.9-2) was installed after preservation. Its initial list was empty. The created Apple-backend VM has UUID 92E6EDFE-441B-452E-A562-2953E0B66FB6. UTM reported a single shared NIC, 4 CPUs, 6144 MiB, one sparse fixed 32 GiB disk, two read-only installation media and the canonical checkout as its only registry share. The boot configuration confirmed aarch64 Linux and UEFI. Ubuntu Server 24.04.5 ARM64 media matched Canonical SHA-256 f12484464c7d4d73e2167e0a7dbda1e669f8fae9bcf778dc0944937034b320cb. FRR 10.1.1 remote manifest inspection confirmed linux/arm64 digest sha256:c056a150c20af08bd2068f79ef1192d20cc501d22551b1af74db667e7530b80b.
