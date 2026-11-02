# Salkku – salattu kurssiseuranta

Henkilökohtainen osake- ja rahastoseuranta GitHub Pagesissa: päivän katsaus, ostotutka,
oma salkku euroina ja push-ilmoitukset puhelimeen. Seurantalista, omistukset ja kurssit on
salattu (AES-256-GCM, avain PBKDF2-SHA256 600 000 kierrosta) salasanalla, joka on vain
repositorion salaisuudessa `SALKKU_PASSWORD`. Laitteille tallennetaan vain salasanasta johdettu
avain, jota ei voi lukea ulos. Repositoriossa ja julkaistulla sivulla on vain salattua dataa.

| Tiedosto | Tarkoitus |
|---|---|
| `index.html` | Sovellus |
| `sw.js` | Vastaanottaa push-ilmoitukset |
| `symbols.enc` | Salattu seurantalista ja asetukset – sovellus luo ja päivittää tämän |
| `scripts/update_data.py` | Hakee kurssit Yahoo Financesta, laskee salkun ja signaalit, salaa tuloksen |
| `scripts/signals.py` | Ostotutkan signaalit ja niiden historiallinen osuvuus |
| `scripts/notify.py` | Push-ilmoitukset (aamukatsaus, signaalit, tavoitehinnat, häiriöt) |
| `scripts/search.py` | Sovelluksen arvopaperihaku |
| `scripts/common.py` | Salaus ja yhteiset apufunktiot |
| `.github/workflows/update.yml` | Kurssihaku 5 min välein pörssipäivinä + julkaisu |
| `.github/workflows/search.yml` | Arvopaperihaku (tulos haaraan `search-results`) |
| `requirements.txt` | Python-kirjastot |
| `manifest.webmanifest`, `icons/` | Kotivalikko-sovelluksen nimi ja kuvake |

## Käyttöönotto lyhyesti

1. Lataa tiedostot julkiseen repositorioon.
2. *Settings → Secrets and variables → Actions → New repository secret*:
   nimi `SALKKU_PASSWORD`, arvo = pitkä salasana.
3. *Settings → Pages → Source: GitHub Actions*.
4. *Actions → Päivitä kurssit → Run workflow*.
5. Avaa `https://<käyttäjä>.github.io/<repo>/`, anna salasana, lisää GitHub-token
   (*asetukset → GitHub-yhteys*) ja lisää seurattavat kohteet.

Token: fine-grained, vain tähän repositorioon, *Contents: Read and write* ja
*Actions: Read and write*. Token tallentuu vain laitteen selaimeen.

## Ostotutka

Lasketaan osinko-oikaistuista päätöskursseista: RSI(14) < 30 → +2, < 40 → +1;
pudotus 52 vk huipusta ≥ 1,5 × kohteen tavallinen 3 kk heilahdus → +2, ≥ 1,0 × → +1;
Bollingerin alanauhan alla → +1; notkahdus nousutrendissä tai nouseva MA200 tukena → +1;
laskutrendi → −1; oma tavoitehinta alittunut → +3. ≥ 2 = mahdollinen, ≥ 4 = vahva.
Jokaiselle kohteelle näytetään, miten kurssi on kehittynyt 3 kk aiempien signaalien jälkeen.
Signaalit ovat teknisiä laskelmia, eivät sijoitusneuvontaa.

Actions-lokeihin ei kirjoiteta kohteiden nimiä, tunnuksia, määrää eikä hakusanoja.
