# UTM resource measurements

| Resource | Observed or configured value |
|---|---|
| Host | Apple Silicon arm64, macOS 26.5 (25F71), 11 logical CPUs, 18 GiB physical RAM |
| Available host disk | 109.51 GiB before provisioning, approximately 97.28 GiB after validation |
| Virtual hardware | Apple virtualization, UEFI, 4 vCPUs, 6144 MiB memory, one 32 GiB sparse VirtIO disk |
| Allocated disk image blocks | 4,179,592 KiB, approximately 3.99 GiB on macOS |
| Guest filesystem | 31 GiB ext4 root, 7.2 GiB used, 22 GiB available after deployment |
| Installer | 3.56 GiB ARM64 ISO, detached and stored outside Git |
| Network | One shared NAT adapter; six Docker-internal lab segments |

The CPU allocation is below half the host's logical CPU count. RAM uses one third of physical memory. UTM 5.0.9 stores this VM under its application container. Use du on the .img for allocated space and ls -lh for virtual capacity. Host free-space changes include other host activity. ISO media and scoped setup logs are in /Users/zap/Documents/NetGuard-VM-media. Keep the separate preservation archive when retiring installation media.
