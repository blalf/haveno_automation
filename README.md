## 1. System dependencies

```bash
sudo apt install -y python3-venv python3-tk
```

(`python3-tk` is required by the GUI library — without it the app
crashes on startup.)

---

## 2. Download the Haveno daemon

The `.deb` does not include a daemon binary. Download it from the
Retoswap GitHub releases, **matching the exact version of your
installed Haveno**:

1. Check your Haveno version (visible in the desktop's "About"
   screen, or `ls /opt/haveno/lib/app/`).
2. Go to https://github.com/retoaccess1/haveno-reto/releases, open
   the release that matches your version, and download
   `daemon-linux-x86_64.jar`.
3. Put it somewhere convenient, e.g.:
   ```bash
   mkdir -p ~/haveno-daemon
   mv ~/Downloads/daemon-linux-x86_64.jar ~/haveno-daemon/
   ```

You do **not** need to install Java — the Haveno `.deb` bundles its
own JRE at `/opt/haveno/lib/runtime/bin/java`. Use that path.

---

## 3. Find your appName

For the daemon to see the same wallet and payment accounts as the
desktop, both must launch with the same `--appName`. Find what the
desktop uses:

```bash
ls ~/.local/share/ | grep -i haveno
```

The folder name is your appName (e.g. `Haveno`, `haveno-reto`, etc.).
Note it — you'll plug it into the daemon command below.

---

## 4. Run the daemon

Replace `Name` with your appName from step 3, and adjust the JAR path
if needed:

```bash
/opt/haveno/lib/runtime/bin/java -jar ~/haveno-daemon/daemon-linux-x86_64.jar \
  --appName=Name \
  --apiPort=1202 \
  --apiPassword=apitest \
  --torControlPort=9051 \
  --torControlCookieFile=/run/tor/control.authcookie \
  --torControlUseSafeCookieAuth \
  --socks5ProxyXmrAddress=127.0.0.1:9050 \
  --torStreamIsolation \
  --useTorForXmr=on \
  --disableRateLimits=true
```

⚠️ **Never run the Haveno desktop and the daemon at the same time.**
They share the same wallet, and Haveno locks it — the second process
will crash. Workflow: use the desktop to set up your wallet / payment
accounts / review trades visually, then close it and run the daemon
when you want the app to publish offers.

Leave the daemon terminal running. Initial sync (Tor + P2P) can take
a couple of minutes.

### bwrap note

If you normally run Haveno inside a `bwrap` sandbox, launch the
daemon inside the same sandbox (copy your wrapper script, replace the
`/opt/haveno/bin/Haveno` invocation with the `java -jar …` command
above). As long as your bwrap shares network with the host (the
default — no `--unshare-net`), the app runs **outside** the sandbox
and reaches `localhost:1202` normally, nothing else to configure.

If your bwrap uses `--unshare-net`, let me know and I'll adapt — the
app would need to run inside the same namespace.

---

## 5. Set up the app (first time)

In a new terminal, from the folder where you extracted the app:

```bash
cd haveno-automation
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 6. Launch the app

```bash
cd haveno-automation
source venv/bin/activate
python3 src/app.py
```

On the **Connection** tab, the fields are pre-filled
(`localhost` / `1202` / `apitest`). Just fill in:

- **Account password**: the wallet unlock password you set the first
  time you opened Haveno desktop.

Click **Save & Reconnect**. The status dot in the top bar should turn
green within a few seconds.

If it stays red, check:
- The daemon terminal — is it still running and past initial sync?
- The API password matches `apitest` (or whatever you used in
  step 4).
- The appName matches what the daemon is running under.
