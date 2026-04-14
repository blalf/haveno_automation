#!/bin/bash
###############################################################################
#  SETUP COMPLET — Haveno Stagenet + haveno-automation
#  Kali Linux / Debian
#
#  UTILISATION :
#    Ne lance PAS ce script d'un coup. Copie-colle chaque ÉTAPE une par une
#    dans ton terminal car certaines nécessitent de redémarrer le shell.
#
#  Dossier de travail : ~/Bureau/taff/automation/files
###############################################################################

WORKDIR="$HOME/Bureau/taff/automation/files"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "=============================================="
echo " ÉTAPE 1 — Dépendances système"
echo "=============================================="
# Installe les outils de base
sudo apt update
sudo apt install -y make wget git curl python3 python3-pip python3-venv

echo ""
echo "=============================================="
echo " ÉTAPE 2 — Java 21 (via SDKMAN)"
echo "=============================================="
echo " /!\\ APRÈS cette commande, FERME et ROUVRE ton terminal"
echo ""
# Installe SDKMAN (gestionnaire de JDK)
curl -s "https://get.sdkman.io" | bash

# >>> FERME ET ROUVRE TON TERMINAL ICI <<<
# Puis lance :
#   sdk install java 21.0.9.fx-librca
#
# Vérifie avec :
#   java -version
#   (doit afficher OpenJDK 21)


echo ""
echo "=============================================="
echo " ÉTAPE 3 — Cloner et compiler Haveno"
echo "=============================================="
cd "$WORKDIR"

# Clone le repo officiel Haveno
git clone https://github.com/haveno-dex/haveno.git
cd haveno

# Compile (sans les tests pour aller plus vite)
make skip-tests

echo ""
echo "=============================================="
echo " ÉTAPE 4 — Lancer Haveno en stagenet"
echo "=============================================="
echo ""
echo " Ouvre 3 TERMINAUX SÉPARÉS et lance dans chacun :"
echo ""
echo " Terminal 1 (Seednode) :"
echo "   cd $WORKDIR/haveno && make seednode-stagenet"
echo ""
echo " Terminal 2 (User1 — c'est celui auquel l'app se connecte) :"
echo "   cd $WORKDIR/haveno && make user1-daemon-stagenet"
echo ""
echo " Terminal 3 (User2 — pour tester les trades) :"
echo "   cd $WORKDIR/haveno && make user2-desktop-stagenet"
echo ""
echo " PORTS gRPC :"
echo "   User1 → localhost:3201  (password: apitest)"
echo "   User2 → localhost:3202  (password: apitest)"
echo ""
echo " Attends que les deux instances soient complètement démarrées"
echo " (tu verras des logs qui arrêtent de défiler) avant de passer"
echo " à l'étape suivante."
echo ""


echo ""
echo "=============================================="
echo " ÉTAPE 5 — Installer les dépendances Python"
echo "=============================================="
cd "$WORKDIR/haveno-automation"

# Crée un environnement virtuel Python
python3 -m venv venv
source venv/bin/activate

# Installe les dépendances
pip install -r requirements.txt

echo ""
echo "=============================================="
echo " ÉTAPE 6 — Tester la connexion"
echo "=============================================="
cd "$WORKDIR/haveno-automation"
source venv/bin/activate

# Test rapide — doit afficher la version, les balances, etc.
python3 test_connection.py --host localhost --port 3201 --password apitest

echo ""
echo "=============================================="
echo " ÉTAPE 7 — Lancer l'application GUI"
echo "=============================================="
cd "$WORKDIR/haveno-automation"
source venv/bin/activate

python3 src/app.py

echo ""
echo "=============================================="
echo " TERMINÉ !"
echo "=============================================="
