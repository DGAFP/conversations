# Recette ProConnect staging — 29 octobre 2026

## Livraison initiale

- Code applicatif : `350b3cab361afd69eeab98dfcc2a7970e95c5192`
  (inclut le raccordement `b6fc46e` et les mises à jour de sécurité).
  Ce snapshot livré est conservé par le tag
  `proconnect-staging-20261029-qualified`. Les résultats de cette section
  concernent cette première livraison ; le correctif de connexion locale
  décrit plus bas remplace son comportement de connexion par défaut.
- Backend : `sha256:700350eaee2aafaa39c0cb5d06104628312d41fb9123774ec41dcf676c89b964`.
- Frontend conservé : commit `aa937734a0b92f108717e7183e78279f77b41efb`,
  digest `sha256:2fcfdbc097d2cf24b81d5d674efce7ed621db84e0e1c8fca054807874ebb9831`.
- Chart OCI `conversations:0.35.2`,
  digest `sha256:ca9218f864e7433e75cc2948a1a425e3d1aad517f896d3e3d16b17ad461d474d`.
- Flux : HelmRelease `conversations/conversations`, révision Helm 13,
  `Ready`, `Released`, `TestSuccess` vrais.
- Les deux secrets Secret Manager ont été synchronisés à leur révision 1.
  Aucune valeur n'est versionnée et ESO ne reçoit pas ces identifiants.

La source OCI staging est temporairement **épinglée au digest du chart** pour
qualifier cette branche avant fusion. Après intégration du code à `staging` et
validation de son nouveau chart CI, rétablir la référence habituelle :
`spec.ref: {semver: ">=0.1.0 <1.0.0"}`. Ne pas laisser cette épingle bloquer
les livraisons suivantes. La référence de retour arrière est le chart `0.35.1`,
digest `sha256:a2ba978aa6e88d34aaddc74b71a066ab28818a65a34e69d478f4a884ecab13b5`.
Voir `PROCONNECT.md` pour le retour arrière coordonné avec la sonde.

## Vérifications

- 110 tests ciblés auth/RH initialement réussis, dont signatures RSA réelles,
  nonce, issuer, audience, dates, authorized party et correspondance UserInfo.
- Après correctifs de sécurité : 1 467 tests backend réussis ; quatre tests
  d'URL de stockage signé ont échoué sur le port aléatoire du stockage jetable,
  puis tous quatre ont réussi avec leur domaine de signature attendu.
  Soit **1 471 tests validés**, sans modification de ces tests.
- Six tests de sonde réussis. Ruff sur le code applicatif modifié : réussi.
- Images backend et sonde scannées avec Trivy : **0 HIGH / CRITICAL**.
  Correctifs livrés : Authlib 1.9.1, Brotli 1.2.1, DRF 3.18.3, dnspython 2.9.0,
  pypdf 6.21.2 et mises à jour Alpine du runtime. L'exception au délai uv de
  sept jours est datée et limitée à Brotli.
- Client reconnu par le token endpoint d'intégration : le code factice est
  rejeté par `invalid_grant`, sans exposition des identifiants.
- Parcours navigateur complet avec le FI de test officiel : redirection,
  retour callback, compte créé sans mot de passe, refus avant admission,
  admission temporaire explicite MATTE sans administration, connexion,
  conversation RH et réponse terminée avec sources, logout, retour accueil,
  puis nouvelle authentification exigée par ProConnect.
- Les journaux confirment les chemins callback, logout-callback et formulaire
  technique ; seules leurs occurrences ont été extraites, sans paramètres.
- Sonde externe redéployée sur `/api/v1.0/local/authenticate/` : HTTPS réussi,
  réponse SSE complète et nettoyage réussi. Aucune modification d'Alloy,
  d'admission réseau ou d'alertes dans ce raccordement.

## Comptes et nettoyage

Les sept comptes présents avant la bascule conservent la même empreinte sur
UUID, sujet, admission, activité, flags d'administration, login local et mot
de passe. Un autre nouveau compte ProConnect est apparu pendant la recette :
il reste en attente, sans admission automatique. Aucun rattachement par email
n'a été effectué.

Le compte synthétique a été désactivé, son admission retirée, son groupe de
recette supprimé et sa seule conversation effacée. Les comptes réels requièrent
toujours une admission et les groupes métier attribués par l'administration.
Un ancien compte doit être rapproché manuellement après vérification d'identité
pour conserver son UUID et son historique.

La sonde conserve son compte technique initial, son mot de passe et son seul
corpus MATTE. Son conteneur Serverless est
`7dc03d73-137a-434a-ae64-0453e4278625`, avec l'image
`rg.fr-par.scw.cloud/assistant-rh-staging/edge-probe@sha256:307e0bc94f462c3cf37209d6c5ccb5ff949a4c8e3bbacac655faf112f3026dc5`.

La production n'a pas été modifiée. Les preuves locales ne constituent pas un
résultat de CI : les branches de raccordement sont publiées séparément et ne
sont pas fusionnées.

## Connexion locale par défaut et ProConnect à la demande

Le correctif `a22ce2c9ec1052f612d183b56b21beedc427f7ff` conserve les comptes
locaux actifs et admis sur le formulaire habituel. Un bouton distinct
« Tester ProConnect » permet la recette avec le fournisseur d'intégration.
Le protocole de déconnexion suit la session réellement ouverte.

- Backend :
  `sha256:288801e119af65fefa2fdd11ab645c74aba58d4558d76a67f65c51daacb3150a`.
- Chart `conversations:0.35.3` :
  `sha256:299913ae1de5e6e3415fe185a93840e9c72d63dbcf1c67ee0235482e7362befd`.
- Flux : révision Helm 16 ; `Ready`, `Released`, `TestSuccess` vrais.
- Frontend et sonde conservés. La source OCI staging reste épinglée au nouveau
  digest avec les mêmes consignes de remise en sélection semver après fusion.
- Désactivation temporaire d'OIDC pendant le rétablissement de l'accès, puis
  suppression de cet override après livraison du nouveau backend :
  `ASSISTANT_RH_LOCAL_LOGIN_IS_DEFAULT=true` et OIDC activé dans le chart.
- 143 tests locaux RH, authentification et configuration publique réussis ;
  Ruff, Pylint et Helm lint réussis. Image backend : zéro HIGH/CRITICAL avec
  la base Trivy du 29 octobre.
- Vérifications live : formulaire habituel HTTP 200 et bouton visible depuis
  l'accueil après rechargement ; POST sans CSRF refusé en 403 ; route explicite
  ProConnect en 302 vers l'intégration, avec state, nonce, PKCE S256 et callback
  staging. Aucun jeton ni identifiant n'est extrait dans les preuves.
- Compte local existant vérifié en lecture seule : actif, admis, mot de passe
  utilisable, sans sujet OIDC, habilitations conservées et historique présent.
  Aucun rattachement, changement de mot de passe ou attribution de droit.

Le retour arrière fonctionnel reste la désactivation d'OIDC par le script
`restore-local-login.py`, limité au serveur Kubernetes staging. Le précédent
chart `0.35.2` ne doit être réutilisé qu'avec cet override pour garder l'accès
habituel par mot de passe. Aucune modification de production ni fusion.

## CI de la PR

La PR Conversations est `DGAFP/conversations#10`, la sonde est dans
`DGAFP/assistant-rh-infra#3` (empilée sur `chore/staging-observability`).
Le premier passage CI a signalé le format des commits, le changelog, des mots
français pris pour des fautes anglaises et des avertissements pylint ; ces
points ont été corrigés sans assouplir les contrôles de sécurité.

Le scan global de sources signale aussi six vulnérabilités préexistantes dans
`src/frontend/yarn.lock` inchangé : DOMPurify 3.4.15 (une CRITICAL et une HIGH)
et undici 8.10.2 (quatre HIGH). Correctifs indiqués : DOMPurify 3.4.17 et
undici 8.12.2. Le backend et la sonde restent à zéro HIGH/CRITICAL ; cette preuve
ne couvre pas le frontend conservé. La PR reste en brouillon et ne doit pas être
fusionnée avant traitement de ce contrôle séparé.

Au commit du correctif de connexion locale, les tests backend, le lint backend,
le format des commits et le job de validation Kapsule réussissent en CI. Le scan
global `security-trivy` échoue toujours sur les six vulnérabilités du lock
frontend inchangé. Le succès de la recette staging reste distinct de ce
contrôle de fusion.
