# Documentazione Grass Manager 0.3.0

## Architettura

Tutti i dispositivi sono integrati in una sola istanza Home Assistant. L’add‑on legge gli stati e richiama i servizi di Home Assistant tramite la Supervisor API. Nessuna credenziale Home Assistant viene consegnata a proprietari o giardinieri.

### Impianto

Un impianto è un contenitore logico con:

- cliente, indirizzo e fuso orario;
- entità associate;
- zone;
- programmi;
- utenti autorizzati;
- errori e storico;
- piano di abbonamento.

La separazione viene applicata nelle API del backend, non soltanto nell’interfaccia.

## Ruoli

### Amministratore

Gestisce tutti gli impianti, entità, utenti, assegnazioni, abbonamenti, errori e registro attività.

### Proprietario

Vede il proprio impianto, avvia/arresta, salta zone, modifica programmi e consulta storico/errori.

### Giardiniere

Può essere assegnato a più impianti. Può vedere, avviare, arrestare, saltare zone e consultare storico/errori.

### Manutentore

Oltre ai comandi operativi può verificare entità e prendere in carico gli allarmi.

### Sola lettura

Consulta stato, storico ed errori senza impartire comandi.

## Primo accesso

Le credenziali iniziali provengono dalla configurazione dell’add‑on. La password predefinita di esempio non deve essere usata in produzione. Al primo accesso l’utente amministratore è marcato per cambio password.

## Creazione di un impianto

1. Accedere come amministratore.
2. Aprire **Impianti → Nuovo impianto**.
3. Inserire nome, cliente, indirizzo e piano.
4. Aprire l’impianto.
5. Associare le entità già presenti in Home Assistant.
6. Creare le zone e successivamente i programmi.

## Entità

Ogni entità associata ha un tipo e può essere obbligatoria. Le entità obbligatorie mancanti, `unknown` o `unavailable` generano un allarme visibile agli utenti autorizzati e agli amministratori.

Tipi previsti: valvola, pompa, umidità, pioggia, portata, pressione, meteo e altro.

## Zone

Una zona richiede almeno una valvola comandabile. Sono supportati i domini `switch`, `valve` e `input_boolean`. È possibile associare un sensore di umidità e una soglia oltre la quale la zona viene saltata.

## Programmi

Un programma contiene giorni, orari, partenza opzionale ad alba o tramonto con offset, pompa opzionale, attese e sequenza di zone. Le card mostrano:

- **AUTO** quando giorni e partenze sono validi;
- **MANUALE** quando non è presente una pianificazione;
- **DISABILITATO** quando è salvato ma non schedulato;
- **ERRORE** quando la pianificazione è incompleta.

Durante l’esecuzione sono disponibili **Arresta tutto** e **Salta zona** per la zona corrente o per quelle ancora in attesa.

## Sicurezza operativa

Prima di aprire una valvola il backend verifica che l’entità esista e non sia `unknown`/`unavailable`. Lo stesso controllo viene eseguito sulla pompa. Un errore interrompe il programma, tenta di chiudere la valvola e spegnere la pompa, crea un allarme e invia una notifica configurata.

## Accesso esterno

Ingress è destinato all’amministratore autenticato in Home Assistant. La porta 8099 offre il login interno per proprietari, giardinieri e manutentori. Esporla solo tramite HTTPS o VPN.

## Dati e backup

Il database SQLite risiede in `/data/irrigation_manager.db` ed è incluso nei backup dell’add‑on. Contiene password cifrate con PBKDF2, sessioni, configurazioni, allarmi, storico e audit log.

## Abbonamenti

Per ogni impianto vengono memorizzati piano, stato, prezzo mensile e data di rinnovo. Gli stati suggeriti sono `trial`, `active`, `past_due` e `suspended`. L’integrazione automatica con un provider di pagamento non è inclusa nella 0.3.0.

## Calendario e meteo

La pagina Calendario mostra le prossime partenze fisse e gli eventi solari già disponibili da `sun.sun`. Le entità di tipo meteo associate agli impianti vengono campionate ogni ora e conservate nello storico.
