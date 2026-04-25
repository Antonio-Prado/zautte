#!/bin/sh
# Configura i cron job per la sincronizzazione periodica su FreeBSD.
# Eseguire come utente 'chatbot' (non root).
#
# Uso:
#   sh scripts/cron_setup.sh

CHATBOT_DIR="/opt/chatbot"
VENV_PYTHON="${CHATBOT_DIR}/venv/bin/python"
LOG_DIR="/var/log/chatbot"
CRON_USER="chatbot"

# Crea la directory dei log
mkdir -p "${LOG_DIR}"
chown "${CRON_USER}" "${LOG_DIR}" 2>/dev/null || true

# Funzione che aggiunge una riga al crontab solo se non esiste già
add_cron() {
    line="$1"
    crontab -u "${CRON_USER}" -l 2>/dev/null | grep -qF "${line}" && return
    (crontab -u "${CRON_USER}" -l 2>/dev/null; echo "${line}") \
        | crontab -u "${CRON_USER}" -
    echo "Aggiunto: ${line}"
}

echo "Configurazione cron job per utente '${CRON_USER}'..."

# --- Sync incrementale ogni notte alle 02:30 ---
# Controlla modifiche al sito e indicizza documenti nuovi/aggiornati
add_cron "30 2 * * * cd ${CHATBOT_DIR} && ${VENV_PYTHON} -m scripts.sync incremental >> ${LOG_DIR}/sync_incremental.log 2>&1"

# --- Sync completo ogni domenica alle 03:00 ---
# Riscansiona tutto il sito da zero (rileva anche pagine rimosse)
add_cron "0 3 * * 0 cd ${CHATBOT_DIR} && ${VENV_PYTHON} -m scripts.sync full >> ${LOG_DIR}/sync_full.log 2>&1"

# --- Inbox ogni 30 minuti nelle ore lavorative (lun-ven 8-18) ---
# Processa subito i documenti caricati manualmente
add_cron "*/30 8-18 * * 1-5 cd ${CHATBOT_DIR} && ${VENV_PYTHON} -m scripts.sync inbox >> ${LOG_DIR}/sync_inbox.log 2>&1"

# --- Rotazione log settimanale (domenica alle 04:00) ---
add_cron "0 4 * * 0 find ${LOG_DIR} -name '*.log' -size +10M -exec sh -c 'mv \"\$1\" \"\$1.old\" && gzip \"\$1.old\"' _ {} \;"

echo ""
echo "Cron job configurati. Verifica con: crontab -u ${CRON_USER} -l"
echo ""
echo "Schedule attivo:"
echo "  02:30 ogni notte    → sync incrementale (modifiche)"
echo "  03:00 ogni domenica → sync completo (full crawl)"
echo "  ogni 30min (8-18 lun-ven) → inbox documenti"
