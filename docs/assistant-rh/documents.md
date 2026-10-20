# Consulter les sources sélectionnées — Assistant RH #594

Cette intégration utilise le service documentaire D2 d’[Assistant RH #529](https://github.com/DGAFP/assistant-rh/issues/529), livré dans [la PR API #617](https://github.com/DGAFP/assistant-rh/pull/617), révision vérifiée `b3d3ff781551bdd431b0ed6a8a2754c6c711ead4` (fermeture S3 résistante aux annulations comprise). Elle s’appuie sur l’identité et la délégation individuelles de [#596](identity.md). Elle ne déploie pas le service API et ne valide pas la topologie staging.

## Association autoritative et historique

Le client OpenAI/Pydantic ne conserve pas l’extension propriétaire `x_assistant_rh`. Un lecteur borné inspecte donc les mêmes octets de réponse API en JSON et en SSE, sans reconstruire de références depuis le Markdown, le titre, le message UI ou Langfuse. Les requêtes déléguées demandent `Accept-Encoding: identity` ; une réponse encodée malgré cette demande ne confère aucune association documentaire. Les SDK continuent de traiter le chat normalement.

Seul un terminal API réussi (`finish_reason=stop`, completion canonique et sources valides) fournit les références finales. Le flux API persiste sa source authority avant ce terminal. Un arrêt/échec avant la finalisation réussie côté Conversations ne publie pas d’association. Un message déjà finalisé et enregistré conserve les droits du run terminé, toujours revérifiés à chaque lecture. Les références publiques sont limitées aux URL canoniques Service-Public et Légifrance. Aucune métadonnée de stockage ou champ inconnu n’est conservé. Les trames sont bornées à 1 Mio et les sources à 100 ; les références invalides ou absentes donnent un état indisponible, sans inventer de lien.

La migration `chat.0016_assistantrhmessagerun` crée `AssistantRHMessageRun` : conversation, ID du message assistant créé par le serveur, `completion_id` API et sources finales ordonnées. Message et association sont écrits dans une même transaction après succès. La contrainte unique conversation/message refuse un remplacement accidentel. Ce modèle côté serveur est le point de réutilisation prévu pour #595 ; le feedback n’est pas implémenté ici. La relation est supprimée en cascade avec la conversation. Les anciennes réponses ne sont pas réattribuées ni backfillées depuis leur texte.

Les messages exposent uniquement un titre, l’organisme disponible et un lien public ou applicatif. Les informations absentes (section, extrait, page) ne sont pas inventées ; #223 reste nécessaire pour le repérage précis. Les mêmes parts et métadonnées sont émises dans le flux final et relues depuis la base après rechargement.

## Lecture applicative

`GET /api/v1.0/chats/{conversation}/messages/{message}/sources/{ordinal}/`

Le navigateur fournit des identifiants applicatifs et un ordinal. Les éventuels paramètres `completion_id`, `doc_ref`, clé de stockage ou URL du navigateur ne sont jamais utilisés. Le backend :

1. Vérifie session, admission, compte actif, propriété de la conversation et ministère actuellement habilité.
2. Vérifie la présence du message assistant persisté et résout son association serveur.
3. Relit session et droits avant de signer une délégation fraîche, à usage unique, pour ce GET.
4. Appelle uniquement l’URL API configurée, avec la completion et la référence persistées. Aucun cookie navigateur n’est envoyé à l’API ; aucune redirection ni retry automatique n’est suivi.
5. Transmet progressivement le PDF avec `application/pdf`, nom constant `document.pdf`, `private, no-store`, `nosniff`, longueur exacte et `Accept-Ranges: none`. Seuls ces headers sûrs sont reconstruits ; `Location` et les headers amont ne sont pas relayés.

`Range` et `If-Range` sont ignorés : chaque demande authentifiée renvoie un PDF complet `200`, conformément à D2 ; aucun 206/416 n’est annoncé. `?download=1` utilise la même autorisation et une disposition `attachment` pour les clients qui demandent explicitement un téléchargement.

Une seule liste « Sources » apparaît directement sous la réponse RH : titre cliquable et organisme disponible, pour les documents comme pour les articles. Le panneau latéral et son bouton ne sont plus proposés en mode RH. Quand les métadonnées autoritatives sont disponibles, la présentation remplace uniquement le bloc réservé `\n\n---\n**Sources :**\n` ajouté par l’API ; les citations du corps de réponse et le texte stocké restent inchangés. Sans association fiable, les références historiques restent affichées sans inventer de lien et un état d’indisponibilité est explicite.

Chaque source ouvre un nouvel onglet. Les PDF internes utilisent l’URL applicative protégée et le lecteur PDF du navigateur ; les articles conservent leur URL canonique publique. Cette présentation remplace le panneau et l’aperçu intégré initiaux selon le cadrage UX du 20 octobre 2026.

## Déploiement et ressources

**ASGI est obligatoire pour cette route.** Sous WSGI, Django matérialiserait un itérateur asynchrone en mémoire : les demandes autorisées sont donc refusées en 503 avant l’appel API. Le Dockerfile par défaut utilise Uvicorn ASGI ; vérifier les éventuelles surcharges Helm et l’ingress en staging.

Huit transferts par processus ; buffers de 64 Kio ; délai total de 120 s depuis l’ouverture, connexion/pool/écriture 5 s, lecture inactive 15 s. La fermeture garde une tâche propriétaire et des budgets séparés de 5 s pour le flux puis pour le client, y compris en cas d’annulations répétées ou avant la première itération ASGI. Les octets déjà délivrés ne sont pas révocables ; un transfert autorisé peut se terminer après retrait de droits, mais chaque nouveau GET est refusé.

Les PDF peuvent être embarqués uniquement depuis le même origin et les origins frontend explicitement configurés dans `CORS_ALLOWED_ORIGINS`, via `Content-Security-Policy: frame-ancestors`. Le PDF est exempté du header global `X-Frame-Options: DENY`. La politique de cookies/session du déploiement doit permettre au frontend de consulter son backend.

Erreurs : 401 pour session absente/expirée ; 403 pour admission/session refusée ; 404 uniforme pour message/document absent ou hors droits ; 503 pour service/configuration indisponible, réponse amont invalide, saturation ou déploiement WSGI. Un refus 401 de l’API avec une délégation tout juste signée est une défaillance de service/configuration, pas une redirection navigateur de connexion. Aucun détail interne ni contenu amont n’est exposé. Après les headers, une panne interrompt le PDF. Aucun bearer/assertion, URL de stockage ou contenu documentaire n’est ajouté aux logs ; les appels journalisent corrélation et statut.

## Preuves et recette restante

Les tests utilisent une base PostgreSQL 17 jetable locale sur `127.0.0.1:55509`, des sessions individuelles synthétiques, des clés Ed25519 temporaires et des réponses API simulées. Ils couvrent le vrai SDK JSON/SSE, la persistance/relecture, les références UI forgées, l’isolation entre utilisateurs du même groupe, la perte de droits/admission, la déconnexion, les headers, l’absence de redirection, les plages ignorées et la fermeture bornée. Les tests frontend couvrent le lien unique par source, le nouvel onglet, l’absence de lecteur embarqué, les liens publics et les anciennes réponses, la liste mixte documents/articles, le remplacement du seul bloc réservé et l’arrivée tardive des métadonnées.

Avant de clôturer #594 : appliquer cette migration avec le compte de déploiement, déployer les révisions API/fork validées, puis consigner leurs SHA et l’environnement. Tester dans un vrai navigateur après stream, échanges successifs, rechargement et reconnexion ; ouvrir un PDF legacy et un PDF S3 réel depuis la liste des sources dans un nouvel onglet ; vérifier les liens publics, les erreurs, un gros PDF, l’annulation, le logout/désactivation et le retrait/rétablissement des droits. Vérifier ingress/headers/cookies, identité et réseau privé. Les preuves locales ne remplacent pas cette recette staging ; aucun compte runtime ni bucket n’est modifié par cette branche.


Validation locale du 20 octobre 2026 : **217 tests backend ciblés** (identité #596, client Pydantic-AI et documents #594), **429 tests frontend**, TypeScript et lint des fichiers concernés. Le test ASGI utilise un vrai socket Uvicorn, une session Django et un PDF synthétique de **12 582 912 octets**, avec SHA-256 et abandon en cours de transfert ; le service D2 amont y est simulé. Les tests de fermeture S3 API de la révision #617 citée ci-dessus passent également (**14 tests**). Les tests ne contactent ni RAG distant, ni S3 réel, ni ProConnect.
