# NetGuard recruiter replay

The public replay starts with the healthy Ubuntu ARM64 fabric. Choose **Play incident** or use the stage selector. Click a router to inspect its recorded BGP peers, interfaces, and kernel routes. The probe table shows ICMP and HTTP in both directions and the recorded next-hop candidates. [Open demo](https://netguard-recruiter-demo.vercel.app/)

The recording shows the Leaf 1–Spine 1 link failure, Qwen's diagnosis and cited observations, verified failover through Spine 2 at 12.1 seconds, and restored redundancy at 16.4 seconds. The lab controller recorded an automatic restoration after the alternate path passed its checks. [Source recording](proof/p3/final-recovery.json)

## Build and publish

    npm ci --prefix web
    npm --prefix web run build:demo
    python3 -m http.server 8000 --bind 127.0.0.1 --directory web/dist-demo

The build selects five stages from the Ubuntu recording and bundles their evidence with the browser UI. The GitHub workflow on the fork's `main` branch checks out `netguard-ubuntu-arm64`, runs the same build, and publishes the static output to Vercel. Select **Run workflow** to publish. [Deploy recruiter demo](https://github.com/giaphoang/netguard/actions/workflows/deploy-recruiter-demo.yml)

The workflow uses the `recruiter-demo` GitHub environment. It reads the `VERCEL_TOKEN` project credential and the `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` and `NETGUARD_DEMO_URL` variables. The job compares the production HTML and JavaScript with the files it built.

## Verify

Run the browser smoke check with `NODE_PATH` pointing to a local `playwright-core` installation and Chrome available.

    node scripts/verify_recruiter_demo.cjs http://127.0.0.1:8000/
    node scripts/verify_recruiter_demo.cjs https://netguard-recruiter-demo.vercel.app/

On November 21, 2026, Chrome verified playback, probe outcomes, the saved Qwen decision, router inspection, and viewport fit at 1440 and 390 pixels. The public link worked in a fresh session with the Ubuntu guest stopped. The normal local dashboard build and all 113 Python tests passed. The live SSH dashboard separately showed bidirectional ICMP and HTTP success on the Ubuntu guest, and a read-only Agent request completed through Mac Ollama with `qwen2.5:1.5b`. [Live setup](UTM_ARM64_SETUP.md)

The manual deploy on `main` built Ubuntu branch commit `f2d7cee`, published it to Vercel, and matched the public HTML and JavaScript byte for byte with the build. [Successful deploy run](https://github.com/giaphoang/netguard/actions/runs/44881925852)
