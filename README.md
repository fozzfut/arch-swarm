# ArchSwarm

Multi-agent architecture brainstorming. Agents with different perspectives (simplicity, modularity, reusability, scalability) debate design decisions.

## Install

```bash
pip install arch-swarm-ai
```

## Usage

```bash
# Analyze project architecture
arch-swarm analyze . --scope src/

# Start a design debate
arch-swarm debate . --topic "How to reduce coupling in server.py?"

# View debate results
arch-swarm report <session-id>
```

## Agent Roles

| Role | Focus |
|------|-------|
| Simplicity Critic | Less is more. Flags over-engineering. |
| Modularity Expert | Clean boundaries, single responsibility. |
| Reuse Finder | Finds duplication, suggests abstractions. |
| Scalability Critic | Will this scale? Performance bottlenecks? |
| Trade-off Mediator | Synthesizes perspectives, proposes compromises. |

## License

MIT
