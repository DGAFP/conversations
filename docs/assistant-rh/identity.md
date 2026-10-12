# Identité et habilitations Assistant RH

Adaptation du fork `DGAFP/conversations`, suivie dans
[Assistant RH #596](https://github.com/DGAFP/assistant-rh/issues/596).
Base : `main` à `cd1b2894312c691fc351d77093e54cc61b7af946`.
Maintien prévu : @pcasad. La licence MIT upstream reste applicable.

Les principes viennent de l’extrait validé du DAT communiqué le 12 octobre 2026.
Le [contrat API](https://github.com/DGAFP/assistant-rh/blob/dev/docs/architecture/hexagonal-split/14-conversations-delegation-contract.md)
décrit le profil technique livré ; il ne prouve pas une recette ProConnect.

## Configuration serveur

Le mode est désactivé par défaut pour conserver les parcours upstream. Son
activation exige les deux migrations Django ajoutées et une nouvelle connexion
OIDC des utilisateurs ; les anciennes sessions n’ont pas le nonce d’audit.
Aucune admission ni habilitation existante n’est déduite ou reprise automatiquement.

```dotenv
ASSISTANT_RH_ENABLED=true
ASSISTANT_RH_API_URL=http://assistant-rh-api:8000/v1
ASSISTANT_RH_DELEGATION_ISSUER=conversations-staging
ASSISTANT_RH_API_AUDIENCE=assistant-rh-api-staging
ASSISTANT_RH_SIGNING_KEY_FILE=/run/secrets/rh-signing.pem
ASSISTANT_RH_SIGNING_KID=staging-20261012
ASSISTANT_RH_AUDIT_KEY_FILE=/run/secrets/rh-audit.key
OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION=false
```

Les fichiers secrets sont montés uniquement dans le backend : clé privée
Ed25519 au format PEM PKCS8 et clé d’audit aléatoire d’au moins 32 octets.
Ne pas utiliser la clé de signature comme clé HMAC. Ni les clés, ni les assertions,
ni le nonce de session ne sont retournés dans `/config/` ou le catalogue.
Le nom `sub` transmis à l’API désigne l’UUID interne Conversations, pas le sujet OIDC.
La recherche OIDC ne fusionne jamais les comptes par email dans ce mode, même si
la configuration historique autorise ce fallback.

L’API reçoit uniquement les clés **publiques** dans `CONVERSATIONS_DELEGATION_JWKS`,
le même émetteur dans `CONVERSATIONS_DELEGATION_ISSUER` et la même audience dans
`ASSISTANT_RH_API_AUDIENCE`. Appliquer les migrations et provisionner les comptes
runtime non propriétaires selon le
[runbook API](https://github.com/DGAFP/assistant-rh/blob/dev/docs/deployment/SCALEWAY_DB_RUNTIME_ROLES.md)
avant activation. La base Conversations est distincte de la base RAG ; ce fork
n’accède jamais à la base RAG. L’API doit être joignable uniquement depuis le
backend par réseau privé ; vérifier la topologie réelle, aucune règle réseau
n’est installée par ce code.

Chaque modèle logique du fichier LLM doit utiliser le provider `assistant-rh` ;
son `hrid` et son `model_name` sont identiques. Exemple minimal :

```json
{
  "providers": [{
    "hrid": "assistant-rh",
    "kind": "assistant-rh",
    "base_url": "http://assistant-rh-api:8000/v1",
    "api_key": "unused"
  }],
  "models": [{
    "hrid": "assistant-rh-matte",
    "model_name": "assistant-rh-matte",
    "human_readable_name": "MATTE",
    "provider_name": "assistant-rh",
    "is_active": true,
    "supports_streaming": true,
    "system_prompt": "",
    "tools": []
  }]
}
```

L’URL effective vient d’`ASSISTANT_RH_API_URL`. La valeur `api_key` est un
placeholder de compatibilité du schéma upstream, remplacée à l’envoi par la
délégation. Ne jamais y placer un bearer ou une clé de groupe B4.
Les codes reconnus sont `masa`, `matte`, `mi`, `mso` : recouper ce catalogue avec
l’API lors de la recette. Un modèle configuré n’accorde aucun droit à lui seul.

## Administration et admission

1. Un utilisateur se connecte via ProConnect/OIDC. Son compte interne est créé
   sans admission et sans corpus, puis redirigé vers la page d’accès refusé.
2. Un administrateur central DGAFP, représenté par un superutilisateur Django,
   valide `assistant_rh_admitted` dans l’administration du compte. La validation
   centrale est la voie livrée ; il n’est pas nécessaire de préparer une invitation.
3. L’administrateur crée les groupes et les `MinistryGrant` (groupe → ministère),
   puis affecte les utilisateurs aux groupes dans l’administration des comptes.
4. Après reconnexion, le catalogue est l’union des corpus accordés par les groupes,
   recoupée avec les modèles configurés et actifs. Sans habilitation, il est vide.

Le superutilisateur n’obtient pas automatiquement d’habilitation à discuter.
Les modifications d’admission, d’appartenance et de modèles par groupe passent
par l’admin Django, dont `LogEntry` conserve acteur, date, objet et champs changés.
Les administrateurs non centraux ne peuvent pas modifier ces habilitations.
Les scripts SQL et les changements directs via l’ORM ne constituent pas un
service d’administration audité et ne sont pas la procédure d’exploitation.

## Contrôles par requête

Le modèle logique choisi à la création fixe `ministry` et `model_hrid` côté
serveur. Les demandes de changement sont refusées. Les listes, la recherche,
les lectures et les conversations imbriquées dans les projets sont filtrées par
**auteur + habilitation courante**. Le retrait masque l’historique sans le supprimer ;
le rétablissement du ministère, même via un autre groupe, restaure son accès.
Les anciennes conversations sans ministère ne sont pas réattribuées.

Chaque envoi HTTP relit session, admission et droits, puis signe un JWS EdDSA avec
`sub`, `models`, `ministry`, `iss`, `aud`, `iat`, `exp` (120 s), un `jti` neuf et
`audit_session`. Ce dernier est le HMAC-SHA-256 d’un nonce aléatoire interne,
renouvelé à chaque connexion OIDC, avec une clé dédiée. Logout, expiration,
désactivation, retrait d’admission et changement du mot de passe invalident
l’émission suivante. La désactivation ou le retrait d’admission par le service
d’administration révoque aussi les sessions antérieures après réactivation. Les retries automatiques du SDK sont désactivés ; un nouvel
essai utilisateur produit une nouvelle assertion. Un timeout du chat ne garantit
pas que le run n’a pas déjà été créé.

Le client ne suit pas les redirections et ne signe que la route chat du backend
configuré, avec le modèle de la conversation. Le changement Albert/Scaleway
appartient à l’API et ne change pas le ministère. Les modèles upstream, leurs
outils, leurs cascades de secours et leur résumé/titre par inférence séparée
ne sont pas utilisés dans le mode RH. Le titre initial vient de la question.

Les journaux backend portent uniquement un identifiant aléatoire par tentative,
envoyé dans `X-Client-Request-Id`. La PR API associée le reprend dans `X-Request-Id`
et les logs. Aucune assertion ou contenu de délégation n’est journalisé.
Le chiffrement, la rétention et la sécurité du stockage des sessions relèvent
de la configuration d’exploitation Conversations existante.

## Rotation et opérations en cours

Ajouter la nouvelle clé publique avec un nouveau `kid` côté API et redéployer ;
basculer le signataire Conversations ; retirer l’ancienne clé publique et
redéployer l’API. Le retrait d’un `kid` prend effet au redéploiement, sans
rechargement à chaud. Documenter responsable et procédure de rotation par
environnement. Une réponse déjà autorisée peut finir après un retrait de droits ;
la prochaine requête est refusée. Les octets déjà transmis ne sont pas révocables.
Un backend Conversations compromis peut signer au nom des utilisateurs : limite
explicite du mécanisme, réduite par le réseau privé et la protection des clés.

## Limites et recette restante

Les pièces jointes et leurs anciennes URLs présignées sont désactivées côté
serveur et masquées côté UI dans ce mode. Le proxy documentaire avec contrôle
courant, bucket privé et absence de lien S3 navigateur relève de #594. Les routes
Assistant RH de feedback et la correspondance message/completion relèvent de #595 ;
le scoring Langfuse historique est refusé et les traces Langfuse sont désactivées
dans ce mode. Ce parcours n’est pas une intégration des routes de feedback RH.

Les tests locaux couvrent admission, multi-groupes, catalogue vide, isolation,
ministère fixe, retrait/rétablissement de l’historique, identité à la reconnexion,
expiration/logout/désactivation, pseudonyme d’audit, signature et appels SDK JSON/SSE.
Ils utilisent un PostgreSQL jetable et un transport IA simulé. La CI upstream
exécute les tests ajoutés et les vérifications de build existantes.

Avant clôture de #596, consigner commit du fork, commit API, environnement et
résultats de la recette réelle : login ProConnect, catalogue, chat JSON/SSE et
run attribué, isolation des feedbacks, anti-rejeu partagé entre réplicas, grants
runtime réels, topologie privée et documents protégés. La fusion seule et les
mocks ne valident pas cette recette. B4 reste une coexistence distincte.

## Synchronisation upstream

Les adaptations RH sont isolées derrière `ASSISTANT_RH_ENABLED`. Mettre à jour
`main` du fork par une PR de synchronisation upstream, puis vérifier les migrations,
le provider PydanticAI/OpenAI, le schéma des messages et les permissions de tous
les chemins d’historique. Exécuter la CI et la recette RH avant promotion ; ne
pas publier un deuxième jeu de patchs pour Compose. #593 doit épingler le commit
validé du fork. Les adaptations visuelles restent suivies dans #595.
