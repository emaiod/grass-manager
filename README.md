# Irrigation Manager Multi‑Impianto

Portale multiutente per gestire molti impianti di irrigazione attraverso **un’unica installazione Home Assistant**.

Home Assistant resta il livello hardware: Shelly, eWeLink/Sonoff, Zigbee, ESPHome, MQTT e le altre integrazioni continuano a creare e mantenere le entità. Irrigation Manager organizza tali entità in impianti separati e applica utenti, ruoli, programmazione, sicurezza, monitoraggio e storico.

## Perché

Un amministratore può gestire decine di abitazioni da un solo server, senza sviluppare driver diversi per ciascuna marca. Ogni cliente vede esclusivamente il proprio impianto; ogni giardiniere vede gli impianti che gli sono stati assegnati; i manutentori ricevono visibilità su guasti e dispositivi offline.

## Funzioni principali

- multi‑impianto sullo stesso Home Assistant;
- utenti interni indipendenti dagli utenti Home Assistant;
- ruoli Admin, Proprietario, Giardiniere, Manutentore e Sola lettura;
- assegnazione dello stesso giardiniere a più impianti;
- valvole, pompe e sensori selezionati dall’elenco delle entità Home Assistant;
- programmi automatici e avvio manuale;
- arresto totale e salto della zona corrente o futura;
- badge AUTO, MANUALE, DISABILITATO ed ERRORE;
- monitoraggio dei dispositivi obbligatori offline/unavailable;
- pagina centralizzata Errori e offline;
- notifiche Home Assistant per avvio, completamento e guasti;
- storico irrigazioni e registro delle operazioni degli utenti;
- anagrafica piano e stato dell’abbonamento per ogni impianto;
- accesso Ingress per amministrazione e porta web dedicata per clienti/operatori.

## Installazione

1. Pubblicare questo contenuto nella radice di un repository GitHub pubblico.
2. In Home Assistant aprire **Impostazioni → App → App Store → Repository**.
3. Aggiungere l’URL del repository.
4. Installare **Irrigation Manager Multi‑Impianto**.
5. Prima del primo avvio impostare username e password iniziali nella scheda Configurazione.
6. Aprire il portale e cambiare subito la password temporanea.

Il portale esterno è esposto sulla porta `8099`. Per accesso Internet usare obbligatoriamente un reverse proxy HTTPS, VPN o servizio equivalente. Non inoltrare direttamente la porta senza TLS.

## Modello commerciale

La tabella abbonamenti consente di associare a ogni impianto piano, stato, canone e rinnovo. La versione 0.3.0 non addebita automaticamente carte: Stripe o altro provider può essere collegato in una release successiva senza cambiare il modello multi‑tenant.

## Stato del progetto

La 0.3.0 è una prima release funzionale del ramo commerciale, separata dall’add‑on monoutente `Irrigazione Centralizzata`.

## Licenza

MIT. Per una distribuzione commerciale hosted è consigliabile definire termini di servizio, privacy policy, SLA e condizioni di abbonamento separati dal codice.
