# Changelog

## 1.0.0

- Nuova generazione del modulo, progettata per installazione pulita.
- Rimossi completamente dagli impianti piano, canone, stato e scadenza dell'abbonamento.
- Abbonamenti associati esclusivamente all'anagrafica utente.
- Tipi di accesso: gratuito, abbonamento con scadenza e lifetime.
- Stato calcolato dell'abbonamento: attivo, prova, lifetime, sospeso, annullato o scaduto.
- Disattivazione automatica dell'anagrafica e delle sessioni alla scadenza configurata.
- Riattivazione manuale oppure automatica quando viene registrato un nuovo periodo pagato.
- Storico pagamenti per utente con importo, valuta, stato, metodo, riferimento e periodo coperto.
- Gestione completa utenti: modifica, disattivazione ed eliminazione.
- Piani Base e Premium modificabili.
- Gli impianti contengono solo dati tecnici e operativi.
- Portale esterno sulla porta 8100 con credenziali Grass Manager.

## 0.4.1

- Prima pagina abbonamenti per proprietario.
- Modifica ed eliminazione utenti.

## 0.3.0

- Primo ramo multi-impianto e multiutente.
- Utenti interni con sessioni protette e password PBKDF2.
- Ruoli Admin, Proprietario, Giardiniere, Manutentore e Viewer.
- Assegnazione di più impianti allo stesso operatore.
- Dashboard aggregata con impianti, dispositivi offline e allarmi.
- Associazione delle entità Home Assistant a impianti separati.
- Zone, programmi, pompe e sensori per impianto.
- Storico irrigazioni e audit log utenti.