# Informativa sul trattamento dei dati personali — assistente virtuale "Zautte"

> **BOZZA da sottoporre al DPO/RPD del Comune.** Non pubblicare finché non è approvata.
> I campi tra parentesi quadre vanno completati; le parti marcate "OPZIONE" dipendono
> dal canale scelto per il modello linguistico (API diretta Anthropic oppure AWS Bedrock).
> I tempi di conservazione corrispondono ai valori `RETENTION_*` configurati in `.env`.

Ai sensi degli articoli 13 e 14 del Regolamento (UE) 2016/679 ("GDPR") e dell'art. 4,
comma 3, della Legge 23 settembre 2025, n. 132, il Comune di San Benedetto del Tronto
informa sul trattamento dei dati personali svolto tramite l'assistente virtuale "Zautte".

## 1. Titolare del trattamento

Comune di San Benedetto del Tronto, [indirizzo], PEC [PEC], tel. [telefono].

## 2. Responsabile della protezione dei dati (DPO/RPD)

[Nome o società], contattabile all'indirizzo [email DPO] / PEC [PEC DPO].

## 3. Cos'è Zautte

Zautte è un sistema di intelligenza artificiale che risponde a domande sui contenuti
pubblicati nel sito istituzionale del Comune. Le risposte sono generate automaticamente da
un modello linguistico (Claude, sviluppato da Anthropic) a partire dalle pagine del sito,
non hanno valore provvedimentale e non sostituiscono gli uffici. Il servizio non adotta
decisioni basate unicamente su un trattamento automatizzato ai sensi dell'art. 22 GDPR.

## 4. Dati trattati

- **Testo delle domande e della conversazione.** Il servizio non richiede dati personali e
  chiede di non inserirli. Prima di qualsiasi elaborazione, codice fiscale, IBAN, numeri di
  carta di pagamento, indirizzi email e numeri di telefono vengono sostituiti
  automaticamente da un segnaposto. Altri dati scritti liberamente (es. nomi, indirizzi,
  informazioni sulla salute) non possono essere riconosciuti in modo affidabile: se
  l'utente li inserisce, vengono trattati come parte della domanda.
- **Dati tecnici.** Indirizzo IP, usato solo in memoria per limitare il numero di richieste
  (protezione da abusi) e registrato nei log tecnici del server.
- **Solo per gli utenti abilitati alla fase di sperimentazione** (dipendenti del Comune):
  nome, indirizzo email e password (conservata solo in forma cifrata con funzione di hash),
  data e ora di utilizzo, testo delle domande, eventuali valutazioni (👍/👎) e commenti.

## 5. Finalità e base giuridica

| Finalità | Base giuridica |
|---|---|
| Fornire informazioni sui servizi e sui contenuti del sito comunale | Esecuzione di un compito di interesse pubblico (art. 6, par. 1, lett. e, GDPR; art. 2-ter D.Lgs. 196/2003) — [atto/delibera che istituisce il servizio] |
| Migliorare la qualità delle risposte (analisi delle domande senza risposta e delle segnalazioni) | Come sopra |
| Sicurezza del servizio e prevenzione degli abusi | Come sopra e legittimo interesse alla sicurezza dei sistemi (considerando 49 GDPR) |
| Sperimentazione con i dipendenti: gestione degli accessi, valutazione dell'utilizzo e della qualità | Come sopra; i dati non sono utilizzati per la valutazione della prestazione lavorativa [verificare con il DPO gli adempimenti ex art. 4 L. 300/1970] |

## 6. Conferimento dei dati

L'uso del servizio è facoltativo. Le informazioni sono disponibili anche consultando il sito
e contattando gli uffici comunali.

## 7. Destinatari e responsabili del trattamento

I dati sono trattati da personale del Comune autorizzato e dai seguenti responsabili del
trattamento (art. 28 GDPR):

- **OPZIONE A — API diretta:** Anthropic Ireland, Limited (Dublino, Irlanda), fornitore del
  modello linguistico, che riceve la domanda (già mascherata), la cronologia della
  conversazione e i brani del sito per generare la risposta, in base al Data Processing
  Addendum incorporato nelle condizioni commerciali. Anthropic non utilizza questi dati per
  addestrare i propri modelli e li cancella entro 30 giorni (fino a 2 anni solo per i
  contenuti segnalati dai sistemi automatici come violazione delle regole d'uso).
- **OPZIONE B — AWS Bedrock:** Amazon Web Services EMEA SARL (Lussemburgo), fornitore del
  servizio cloud qualificato ACN su cui viene eseguito il modello linguistico. AWS non
  conserva domande e risposte e non le condivide con lo sviluppatore del modello.
- [Eventuale fornitore dell'infrastruttura che ospita il server del servizio.]

## 8. Trasferimenti di dati fuori dall'Unione europea

- **OPZIONE A — API diretta:** le richieste possono essere elaborate da Anthropic anche al di
  fuori dello Spazio economico europeo, in particolare negli Stati Uniti. Il trasferimento è
  basato sulle clausole contrattuali standard approvate dalla Commissione europea
  (Decisione 2021/914), incluse nel Data Processing Addendum, accompagnate da una
  valutazione d'impatto del trasferimento effettuata dal Comune [riferimento].
- **OPZIONE B — AWS Bedrock:** le richieste sono elaborate esclusivamente in regioni AWS
  situate nell'Unione europea. Non sono previsti trasferimenti di dati verso paesi terzi
  per il funzionamento ordinario del servizio.

## 9. Conservazione

| Dato | Periodo |
|---|---|
| Testo delle domande degli utenti abilitati | 90 giorni; poi restano per 12 mesi solo data, identificativo e metriche di utilizzo |
| Domande senza risposta adeguata (senza indicazione dell'utente) | 180 giorni |
| Valutazioni e commenti | 12 mesi |
| Account degli utenti abilitati | Fino alla fine della sperimentazione o alla revoca dell'abilitazione |
| Log tecnici del server | [14 rotazioni del file di log, secondo la configurazione newsyslog] |
| Dati presso il fornitore del modello | OPZIONE A: fino a 30 giorni; OPZIONE B: nessuna conservazione |

La cancellazione è automatica. [Periodi da confermare con il DPO.]

## 10. Minori

Il servizio non è destinato ai minori di 14 anni, per i quali l'accesso a tecnologie di
intelligenza artificiale richiede il consenso di chi esercita la responsabilità genitoriale
(art. 4, comma 4, L. 132/2025).

## 11. Diritti dell'interessato

L'interessato può chiedere al Comune l'accesso ai propri dati, la rettifica, la
cancellazione, la limitazione del trattamento e opporsi al trattamento (artt. 15-21 GDPR),
scrivendo a [email/PEC] o al DPO. Poiché il servizio non chiede di identificarsi, per le
domande degli utenti non abilitati il Comune potrebbe non essere in grado di identificare i
dati riferiti a una persona (art. 11 GDPR).

L'interessato ha inoltre diritto di proporre reclamo al Garante per la protezione dei dati
personali (www.garanteprivacy.it).

## 12. Come funziona il sistema di intelligenza artificiale

Informazioni sul funzionamento, sui limiti e sul controllo umano del servizio sono
disponibili nella pagina "Come funziona Zautte" ([URL]).

*Ultimo aggiornamento: [data].*
