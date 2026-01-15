# CRUSH.md - Context pour Crush AI Agent

## Projet: Basic Memory

Basic Memory est un système de Personal Knowledge Management (PKM) qui synchronise des notes Markdown avec une base de données sémantique, permettant aux LLMs d'accéder au contexte via MCP.

## Stack Technique

- **Langage**: Python 3.11+
- **Package Manager**: uv
- **Framework**: FastAPI (pour l'API), Click (pour CLI)
- **Database**: SQLite avec FTS5 (full-text search)
- **Protocol**: Model Context Protocol (MCP)
- **Tests**: pytest avec coverage

## Structure du Projet

```
src/basic_memory/
├── api/          # FastAPI endpoints
├── cli/          # Commands Click
├── mcp/          # MCP server implementation
├── services/     # Business logic
├── models/       # SQLAlchemy models
└── sync/         # File synchronization
```

## Commandes Utiles

```bash
# Lancer les tests
uv run pytest

# Lancer avec coverage
uv run pytest --cov=basic_memory

# Lancer le serveur MCP
uv run basic-memory mcp --project main

# Sync des fichiers
uv run basic-memory sync

# Format du code
uv run ruff format .
uv run ruff check . --fix
```

## Conventions

### Code Style
- Utiliser type hints partout
- Docstrings en format Google
- Async/await pour les opérations I/O
- Imports absolus depuis `basic_memory`

### Commits
- Format: `type(scope): description`
- Types: feat, fix, docs, style, refactor, test, chore

### Tests
- Un fichier de test par module
- Nommage: `test_<module>.py`
- Fixtures dans `conftest.py`

## Notes pour l'Agent

1. **Ne jamais modifier** les fichiers dans `.venv/`
2. **Toujours utiliser** `uv run` pour exécuter des commandes Python
3. **Vérifier** les tests avant de commit
4. Le projet utilise **SQLite** - pas de migrations complexes
5. Les notes utilisateur sont dans `/Users/donaldo/basic-memory/` (vault Obsidian)

## Propriétaire

- **Nom**: Donaldo DE SOUSA
- **Rôle**: CEO SoWell, développeur StreetEat
- **Préférence**: Réponses en français, code en anglais
