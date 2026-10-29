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

`/api/v1.0/authenticate/` déclenche ProConnect. Le formulaire technique est à
`/api/v1.0/local/authenticate/` ; il exige CSRF, applique la limitation de débit,
l'admission et une liste fermée d'adresses de connexion. En staging, seul le
compte `observability-staging@assistant-rh.invalid` est autorisé. Les comptes
administrateurs n'ont pas d'exception sur ce formulaire. La connexion à
l'administration Django conserve son propre formulaire existant.

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
