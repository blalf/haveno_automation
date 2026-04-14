# Setup Retoswap (mainnet) + l'app d'automatisation — Kali Linux

Cible : reproduire le setup du client (Retoswap mainnet via Tor) sur
Kali, dans `~/Bureau/taff/haveno_automation/haveno-retoswap`, et
lancer l'app dessus.

**Pourquoi pas le `.deb` ?** Le `.deb` officiel de Retoswap dépend de
libs spécifiques à Ubuntu 24.04 (`libicu74`, `libavcodec60`,
`libmbedcrypto7t64`, etc.) qui n'existent pas sous ces noms-là dans
les repos Kali. → On build depuis les sources, ce qui produit les MÊMES
binaires (`haveno-desktop` et `haveno-daemon`) sans dépendre des libs
système propres à Ubuntu.

---

## 1. Pré-requis système (une fois)

```bash
sudo apt update
sudo apt install -y tor make git curl wget python3 python3-pip python3-venv
```

Java 21 (vérifie d'abord `java -version` — si tu as déjà OpenJDK 21,
skip):

```bash
sudo apt install -y openjdk-21-jdk
java -version    # doit afficher "openjdk version 21.x.x"
```

> Si `apt` n'a pas openjdk-21, utilise SDKMAN (méthode recommandée) :
> ```bash
> curl -s "https://get.sdkman.io" | bash
> # ferme/rouvre le terminal
> sdk install java 21.0.9.fx-librca
> ```

---

## 2. Tor avec ControlPort + cookie auth

Édite `/etc/tor/torrc` (`sudo nano /etc/tor/torrc`) et ajoute (s'ils
n'y sont pas) :

```
ControlPort 9051
CookieAuthentication 1
CookieAuthFileGroupReadable 1
```

Ajoute-toi au groupe `debian-tor` pour pouvoir lire le cookie :

```bash
sudo usermod -aG debian-tor $USER
# Déconnecte-toi puis reconnecte-toi (ou redémarre) — un newgrp ne
# suffit pas toujours pour les sessions GUI.
```

Redémarre Tor et vérifie :

```bash
sudo systemctl restart tor
sudo systemctl enable tor
ls -l /run/tor/control.authcookie
# doit être lisible par le groupe debian-tor (rw-r-----)
```

---

## 3. Cloner et builder Retoswap

```bash
cd ~/Bureau/taff/haveno_automation
# Si haveno-retoswap existe déjà (depuis tes essais précédents avec le .deb), nettoie :
rm -rf haveno-retoswap
git clone https://github.com/retoaccess1/haveno-reto.git haveno-retoswap
cd haveno-retoswap
make skip-tests
```

Le build prend ~10-20 minutes selon la machine. À la fin, vérifie que
les launchers existent à la racine du repo :

```bash
ls haveno-desktop haveno-daemon
# doivent tous les deux être présents et exécutables
```

---

## 4. Lancer le desktop (mainnet user1, Tor — équivalent du client)

Dans un terminal dédié :

```bash
cd ~/Bureau/taff/haveno_automation/haveno-retoswap
./haveno-desktop \
  --baseCurrencyNetwork=XMR_MAINNET \
  --useLocalhostForP2P=false \
  --useDevPrivilegeKeys=false \
  --nodePort=9999 \
  --appName=haveno-reto-XMR_MAINNET_user1 \
  --torControlPort=9051 \
  --torControlCookieFile=/run/tor/control.authcookie \
  --torControlUseSafeCookieAuth \
  --socks5ProxyXmrAddress=127.0.0.1:9050 \
  --torStreamIsolation \
  --useTorForXmr=on \
  --disableRateLimits=true
```

Les flags `--baseCurrencyNetwork=XMR_MAINNET`, `--nodePort=9999`,
`--appName=…` sont l'équivalent build-from-source de ce que le `.deb`
hardcode. Les flags Tor sont copie conforme de la commande prod du
client.

**Au premier lancement** :
1. Le démarrage est lent (Tor + sync P2P) — peut prendre plusieurs
   minutes la première fois.
2. Crée ton compte Haveno (mot de passe d'unlock — note-le, l'app
   en aura besoin).
3. Funde ton wallet XMR à l'adresse affichée (ou skip pour l'instant
   si tu veux juste tester l'API).
4. Configure tes payment accounts (Wise, SEPA, etc.). Ces accounts
   seront ensuite visibles dans l'app pour créer des presets.

---

## 5. Lancer le daemon (port API 1202)

⚠️ **Important :** desktop et daemon partagent le MÊME `--appName`
(donc le MÊME wallet et les MÊMES payment accounts). Haveno locke le
wallet → on ne peut pas lancer les deux en même temps. **Ferme le
desktop avant de lancer le daemon.**

Dans un terminal dédié :

```bash
cd ~/Bureau/taff/haveno_automation/haveno-retoswap
./haveno-daemon \
  --baseCurrencyNetwork=XMR_MAINNET \
  --useLocalhostForP2P=false \
  --useDevPrivilegeKeys=false \
  --nodePort=9999 \
  --appName=haveno-reto-XMR_MAINNET_user1 \
  --apiPort=1202 \
  --apiPassword=apitest \
  --passwordRequired=false \
  --useNativeXmrWallet=false \
  --ignoreLocalXmrNode=false \
  --torControlPort=9051 \
  --torControlCookieFile=/run/tor/control.authcookie \
  --torControlUseSafeCookieAuth \
  --socks5ProxyXmrAddress=127.0.0.1:9050 \
  --torStreamIsolation \
  --useTorForXmr=on \
  --disableRateLimits=true
```

Attends que les logs se stabilisent (le daemon doit se synchroniser
avec le réseau P2P + Tor avant que l'API soit utilisable — souvent
30-60 s).

---

## 6. Tester la connexion à l'API

Dans un autre terminal :

```bash
cd ~/Bureau/taff/haveno_automation/haveno-automation
source venv/bin/activate
python3 test_connection.py --host localhost --port 1202 --password apitest
```

Doit afficher la version du daemon et tes balances. Erreurs courantes :
- `Connection refused` → daemon pas (encore) prêt, attends ou regarde
  ses logs.
- `UNAUTHENTICATED` → mauvais `--apiPassword`.
- `app not initialized` → daemon encore en sync, attends puis retry.

---

## 7. Lancer l'app

```bash
cd ~/Bureau/taff/haveno_automation/haveno-automation
source venv/bin/activate
python3 src/app.py
```

L'onglet **Connection** est pré-rempli avec ces 4 commandes (Desktop,
Daemon, Test, Launch app) — copiables en un clic, modifiables. Les
settings de connexion par défaut pointent sur `localhost:1202` /
`apitest`.

> Si tu vois encore les anciennes commandes (stagenet 3201) c'est que
> ton `config/commands.json` et `config/app_config.json` ont été
> seedés avant ces changements. Supprime-les pour re-seeder :
> ```bash
> rm -f ~/Bureau/taff/haveno_automation/haveno-automation/config/commands.json
> rm -f ~/Bureau/taff/haveno_automation/haveno-automation/config/app_config.json
> ```

---

## Workflow quotidien

1. Lancer le **desktop** quand tu veux changer un payment account, voir
   l'état d'un trade visuellement, ou unlock le wallet pour la
   première fois après un reboot.
2. **Fermer** le desktop.
3. Lancer le **daemon**.
4. Lancer **l'app** → publie tes offres, suit les trades, gère les
   passwords.
