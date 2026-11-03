# ProConnect en staging

Le périmètre est limité à `staging.assistant-rh.beta.gouv.fr` et à la fédération
ProConnect d'intégration. La production conserve sa configuration actuelle.

## Identifiants et redirections

Les valeurs résident dans Scaleway Secret Manager, région `fr-par`, projet
`c1d3f629-d08d-439d-ba76-f115ea06e579`, chemin `/assistant-rh/staging/runtime` :

- `conversations-proconnect-client-id` → `OIDC_RP_CLIENT_ID` ;
- `conversations-proconnect-client-secret` → `OIDC_RP_CLIENT_SECRET`.

Le script `sync-proconnect.py --kubeconfig <staging>` vérifie le cluster et lit
les versions actives sans afficher leur contenu. `--apply` synchronise uniquement
le Secret Kubernetes `conversations-proconnect` via stdin et server-side apply.
Aucune valeur n'est écrite sur disque ni dans une annotation `last-applied`.
Cette synchronisation est manuelle ; elle ne dépend pas d'ESO. Après une rotation,
relancer le script puis redémarrer les workloads qui consomment le Secret.

URLs à enregistrer dans l'espace partenaires ProConnect :

- retour de connexion : `https://staging.assistant-rh.beta.gouv.fr/api/v1.0/callback/` ;
- retour de déconnexion : `https://staging.assistant-rh.beta.gouv.fr/api/v1.0/logout-callback/`.

Le fichier `staging.yaml` contient les paramètres publics. La signature RS256,
le nonce, PKCE S256, l'émetteur, le destinataire, les dates et la concordance du
sujet ID token/UserInfo sont vérifiés. Seul l'ID token nécessaire au logout est
conservé côté session serveur ; ni access token ni refresh token n'est conservé.

## Habilitations manuelles

Une identité ProConnect ne donne aucun droit automatique. Les nouveaux comptes
sont en attente de validation DGAFP. Dans l'administration Assistant RH :

1. vérifier l'identité du demandeur ;
2. activer l'admission Assistant RH ;
3. attribuer les groupes disposant des seuls `MinistryGrant` nécessaires ;
4. vérifier les corpus visibles avec ce compte.

Ne jamais rattacher automatiquement un compte existant par adresse mail. Le sujet
ProConnect est stable dans le périmètre de cet émetteur ; un rattachement à un
ancien compte exige une vérification manuelle du sujet et conserve l'UUID du
compte pour préserver son historique. Ne pas modifier l'émetteur sur une base
existante sans procédure de migration des identités.

## Sonde et retour arrière

Avec `ASSISTANT_RH_LOCAL_LOGIN_IS_DEFAULT=true`, le point d'entrée habituel
`/api/v1.0/authenticate/` présente le formulaire email/mot de passe. Les comptes
locaux actifs et déjà admis conservent leurs identifiants, leur UUID, leurs
groupes et leur historique. Le formulaire conserve CSRF et la limitation de
débit ; il ne réactive ni n'admet aucun compte.

Un bouton distinct « Tester ProConnect » utilise
`/api/v1.0/proconnect/authenticate/`. Cette route exige le mode RH et
`ASSISTANT_RH_OIDC_ENABLED=true`, conserve les contrôles OIDC et permet une
recette avec des identités synthétiques. La déconnexion suit le protocole de
la session réellement ouverte, même lorsque la connexion locale est le défaut.
Aucun compte local n'est rapproché d'une identité synthétique par email.

Si `ASSISTANT_RH_LOCAL_LOGIN_IS_DEFAULT=false` et OIDC est activé, l'entrée
habituelle redevient ProConnect et le formulaire local explicite
`/api/v1.0/local/authenticate/` exige en plus la liste fermée
`ASSISTANT_RH_LOCAL_LOGIN_ALLOWED_EMAILS`. La sonde reste autorisée dans cette
liste. Les comptes administrateurs n'ont pas d'exception sur ce formulaire.
L'administration Django conserve son formulaire existant.

Pour désactiver temporairement ProConnect sur le chart actuellement qualifié,
`restore-local-login.py --kubeconfig <staging> --apply` applique uniquement
l'override `ASSISTANT_RH_OIDC_ENABLED=false` au HelmRelease staging existant.
Le script vérifie le serveur Kubernetes exact et utilise Flux ; il ne change
aucun compte, mot de passe, droit ou secret. Sans `--apply`, il vérifie seulement
le périmètre. Après livraison du backend comprenant le bouton de test, supprimer
cet override temporaire pour retrouver OIDC activé avec le formulaire local
par défaut. Ne pas réactiver le défaut ProConnect avant le raccordement aux
vraies identités.

La sonde externe doit utiliser `PROBE_LOGIN_PATH=/api/v1.0/local/authenticate/`
après livraison du backend. La déconnexion choisit le protocole de la session
réellement ouverte, permettant de conserver la sonde tout en testant le logout
ProConnect.

Livrer un chart OCI scellé avec les digests d'images et laisser Flux effectuer
l'upgrade et son test Helm. Conserver la référence OCI précédente et les valeurs
du HelmRelease pour le rollback. Un rollback complet vers le backend antérieur
nécessite de remettre aussi la sonde sur `/api/v1.0/authenticate/`. Aucune
migration de schéma n'est introduite par cette intégration.

Les tests de recette utilisent le [FI de test officiel](https://partenaires.proconnect.gouv.fr/docs/fournisseur-service/identifiants-fi-test)
avec une identité synthétique. Ne pas admettre de compte réel pendant la recette.
