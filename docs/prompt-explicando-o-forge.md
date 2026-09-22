# PROMPT — O que é o action-vocabulary-forge, o que ele produz e como usar o que ele produz

Use este texto como contexto para qualquer pessoa ou agente que precise entender ou usar o Forge.
Ele responde primeiro a pergunta que mais confunde: **o Forge injeta o JEV em qualquer sistema?**
Resposta curta: **não injeta; ele fabrica a peça que se encaixa.** O Forge produz um pacote
(bundle + adapter gerado) que faz o JEV decidir com segurança dentro de um sistema. O encaixe
final é uma chamada no sistema, feita uma vez, em um único ponto. Tudo o mais é gerado.

---

## 1. Definição em três frases

O action-vocabulary-forge é um compilador de decisões. Ele lê um sistema (código, API, UI, SOPs,
traces), descobre onde esse sistema reduz contexto complexo a uma escolha entre poucas ações, e
compila essas escolhas num **Action Bundle**: estados, ações, transições, precondições, superfícies
de decisão e evidência de cada afirmação. A partir do bundle ele gera um **adapter** que chama o
JEV (TypeSafe System One) para a parte semântica da escolha e mantém em código determinístico
tudo o que é regra: o que é legal agora, thresholds, abstenção, confirmação, execução e log.

## 2. O que o Forge NÃO é

- Não é um agente autônomo. O JEV nunca planeja, nunca chama ferramentas, nunca amplia o conjunto
  de opções. Ele responde uma pergunta fechada por vez.
- Não é um framework que "envelopa" o sistema. O sistema continua sendo o sistema; o Forge entrega
  um módulo Python que o sistema chama num ponto de decisão.
- Não inventa como executar uma ação. Handler só é gerado quando a descoberta registrou, com
  evidência observada, como a ação é invocada (função, request HTTP, comando CLI).
- Não substitui o humano nas decisões de autoridade: credenciais, aprovar o release gate, revisar
  rótulos, resolver abstenções.

## 3. Os dois LLMs e seus papéis (não confundir)

| Quem | Quando | Papel |
|---|---|---|
| Agente de fronteira (Claude Code, Codex, Gemini…) seguindo o `SKILL.md` | descoberta, uma vez por sistema, fora do caminho de produção | lê o sistema, escreve o bundle, registra evidência e bindings, escreve os critérios, rotula casos para revisão |
| JEV (`jev-latest`, TypeSafe) | runtime, a cada decisão | escolhe uma opção entre as legais, com confiança e probabilidades; só isso |
| Código (Forge + adapter gerado) | compilação e runtime | valida o bundle, gera o adapter, filtra ações ilegais, aplica política, executa, loga, calibra, avalia |
| Humano | antes e depois | aprova o gate, revisa rótulos, resolve abstenções |

Inteligência aberta trabalha fora do caminho de produção e deixa artefatos revisáveis.
No caminho de produção só entra inteligência fechada, cercada de código.

## 4. Entrada

Um sistema real e um escopo. Qualquer combinação de: repositório de código, spec de API ou
MCP, telas/DOM, SOPs e documentação, logs e traces de decisões humanas, casos de teste.
O agente de descoberta recebe o caminho e um escopo ("o roteamento de perguntas em
`decision_contracts.resolve`") e produz a pasta `action-bundle/`.

## 5. Saídas: o que cada arquivo é e para que serve

```
action-bundle/
├── surface_candidates.yaml   inventário dos pontos de decisão encontrados (promovidos ou não)
├── action_registry.yaml      o vocabulário: uma entrada por ação, com contrato e binding
├── state_registry.yaml       estados observáveis, cada um com um predicado avaliável por código
├── transition_graph.yaml     de qual estado, qual ação, para qual estado, com guards
├── decision_surfaces.yaml    onde o JEV é chamado: estado de ativação, candidatas, fallback
├── evidence_ledger.jsonl     prova de cada afirmação, com grau (verified_runtime, documented…)
├── jev_adapter_spec.yaml     as perguntas ao JEV (Choice/Noul/Score) + a política (thresholds)
├── coverage_report.md        o que foi inspecionado, o que ficou de fora, pontos cegos
├── generated_adapter.py      (gerado) o módulo que o sistema chama
├── decision_log.jsonl        (gerado em uso) um registro por decisão
└── labels.jsonl              (humano/agente) verdade por caso, para calibrar e avaliar
```

### 5.1 `action_registry.yaml` — uma ação

```yaml
- action_id: route_hedge_coverage_corn
  description: Route the question to contract us-crop-hedge-coverage for corn.
  choose_when: The user asks whether to change, hold, add to, or reduce hedge coverage for US corn.
  do_not_choose_when: Another crop, both crops, a price/basis/P&L question, or no crop at all.
  preconditions:                 # avaliadas por código antes de executar; nunca pelo JEV
    - registry_issue_count == 0
    - contract_status == 'active'
    - historical_market_year == false
  parameters: [{name: contract, required: true}, {name: crop, required: true}, {name: question, required: true}]
  risk: low
  reversible: true
  requires_confirmation: false
  allowed_from_states: [registry_ok]
  destination_states: [contract_routed]
  evidence_refs: [code_resolve, code_render, contract_hedge, test_resolver]
  binding:                       # COMO a ação é invocada; só vira handler com evidência observada
    kind: python_callable        # python_callable | http | cli | mcp | ui
    locator: bellwether.decision_contracts:_render
    arg_mapping: {contract: contract, crop: crops.corn, question: question}
    evidence_refs: [binding_render_observed]
```

Campos que importam para quem usa: `preconditions` (o que o código reavalia), `risk` /
`reversible` / `requires_confirmation` (gates), `binding` (o que o adapter vai executar).

### 5.2 `decision_surfaces.yaml` — onde o JEV entra

```yaml
- surface_id: route_decision_question
  activation: {state_id: registry_ok}          # só neste estado o JEV é consultado
  candidate_actions:
    - {action_id: route_hedge_coverage_corn,     criterion: "…hedge coverage for US corn, and only corn."}
    - {action_id: route_hedge_coverage_soybeans, criterion: "…for US soybeans, and only soybeans."}
    - {action_id: decline_unsupported,           criterion: "Not a hedge-coverage decision for exactly one of corn or soybeans…"}
  fallback_action: decline_unsupported
  abstention_choice: decline_unsupported
  production: true
```

Uma superfície é local: um estado, poucas opções, um fallback seguro. O `criterion` é o único
texto que o JEV lê para cada opção.

### 5.3 `jev_adapter_spec.yaml` — a pergunta e a política

```yaml
model: jev-latest
endpoint: https://api.typesafe.ai/v1/systemone
policy:
  default_when_uncalibrated: abstain     # sem threshold calibrado, a ação abstém
  min_accuracy: 0.97
  questions:
    route_decision_question:
      min_confidence: 0.8                # veio do histórico, não de opinião
      actions: {route_hedge_coverage_soybeans: 0.9}
classifier_questions:
  - question_id: route_decision_question
    surface_id: route_decision_question
    type: choice                         # choice | noul | score
    criteria_source: static              # static | dynamic (escolhas só conhecidas em runtime)
    instruction: "This is the user's decision question… choose decline_unsupported when unsure."
    choices:
      - {id: corn,        criterion: "…", executor_action_id: route_hedge_coverage_corn}
      - {id: soybeans,    criterion: "…", executor_action_id: route_hedge_coverage_soybeans}
      - {id: unsupported, criterion: "…", executor_action_id: decline_unsupported}
    abstention_choice: unsupported
```

### 5.4 `evidence_ledger.jsonl` — a prova

```json
{"evidence_id":"binding_render_observed","source_type":"test","locator":"tests/test_decision_contracts.py:12-44",
 "claim":"_render was observed running (via resolve) producing the three selectors…","grade":"verified_runtime"}
```

Graus: `verified_runtime`, `verified_schema`, `documented`, `observed_trace` (aceitos em produção);
`inferred`, `hypothetical` (nunca em produção). Handler real exige `verified_runtime` ou
`observed_trace` no binding.

### 5.5 `generated_adapter.py` — o módulo que o sistema chama

API pública (tudo gerado a partir do bundle; nada é escrito à mão):

```python
legal_actions(state) -> [action_id]         # allowed_from_states + preconditions
infer_state(state) -> state_id | None       # pelo observable_predicate
check_preconditions(action_id, state) -> [predicados que falharam]

build_payload(context, question_id=, dynamic_choices=)   # a pergunta ao JEV
parse_response(response, question_id)      # IllegalChoice se a resposta sair do conjunto
apply_policy(decision)                      # threshold por ação/pergunta/global; abstém se não calibrado
threshold_for(question_id, action_id)

execute(decision, state, handlers=, guard=, confirmed=, log=, case_id=)
    # ordem dos gates: ação existe → estado permitido → requires_confirmation → precondições
    # → guard do host → abstenção cai num fallback declarado → handler existe → executa → loga
classify(context, ...)                      # JEV + política
decide(context, state, ...)                 # classify + log, sem executar (modo sombra)
run(context, state, ...) -> (Decision, result)   # decide + execute, 1 registro de log

HANDLERS        # {action_id: callable(state)} gerado dos bindings; sobrescreva para trocar um stub
HANDLER_STATUS  # {action_id: "generated:python_callable" | "stub:no_binding" | "stub:unobserved_binding" | …}
DecisionLog(path)
```

### 5.6 `decision_log.jsonl` — um registro por decisão

`case_id, state_id, selected_choice, proposed_action_id` (o que o JEV propôs), `action_id`
(o que o código executou), `confidence, threshold, abstained, reason, legal_actions, executed,
outcome, handler_status, latency_ms, usage, ground_truth_action_id`. É a entrada da calibração e
do eval.

## 6. Como usar as saídas, por papel

### 6.1 Desenvolvedor do sistema (o encaixe, feito uma vez)

1. Monte o **estado observável** que os predicados do bundle leem. É um dict com os campos que
   as precondições citam (`registry_issue_count`, `contract_status`…) mais os objetos que os
   bindings precisam (`contract`, `crops`). Isso é um shim pequeno e determinístico
   (no bellwether: `decision_router.build_state`, ~30 linhas).
2. No ponto de decisão do sistema, troque a lógica atual por uma chamada:
   ```python
   decision, result = adapter.run(context, state, case_id=..., log=DecisionLog(path))
   ```
   Se `len(adapter.legal_actions(state)) == 1`, execute direto sem chamar o JEV.
3. Mantenha a lógica antiga como **fallback e oráculo**: qualquer erro do adapter cai nela, e o
   resultado dela vai para o log ao lado da decisão do JEV (`oracle_action_id`).
4. Ative por flag (`BELLWETHER_JEV_ROUTING=1`). Desligado = comportamento antigo, byte a byte.
5. Se um handler é `stub:*`, ou você acrescenta a evidência que falta ao ledger e regenera, ou
   sobrescreve `HANDLERS[action_id] = sua_função`.

Tempo típico: 1 a 3 horas para o shim, a troca da chamada e os testes com transporte fake.

### 6.2 Operador / dono do sistema (o ciclo de vida)

```
1. rode em sombra:        adapter.decide(...) loga sem executar
2. rotule os casos:       labels.jsonl  (case_id, question_id, ground_truth_action_id)
3. calibre:               calibrate_thresholds.py ./action-bundle --log decision_log.jsonl --labels labels.jsonl --write
4. regenere:              generate_adapter.py ./action-bundle --output ./action-bundle/generated_adapter.py
5. avalie:                evaluate_decisions.py ./action-bundle --log decision_log.jsonl --labels labels.jsonl
                          → métricas + RELEASE GATE: APPROVE | HOLD
6. responda ao gate.      só o humano libera; o script nunca ativa nada
7. em produção:           resolva abstenções; elas viram rótulos; volte ao passo 3
```

O gate segura quando: há ação ilegal no held-out; falta caminho de abstenção; uma ação exercida
não tem threshold; boundary ou abstention accuracy abaixo do mínimo; amostra pequena; bundle
inválido.

### 6.3 Agente que vai criar um bundle para outro sistema

Invoque a skill `action-vocabulary-forge` com o caminho e o escopo. Regras que ela impõe:
- todo item precisa de evidência com localizador; `inferred` não entra em produção;
- toda ação executável precisa de `binding` com evidência **observada**;
- o que é regra vira predicado (precondição), nunca critério do JEV;
- toda superfície tem fallback seguro (reversível, não crítico);
- segredos nunca entram no bundle; use `auth_env`.
Valide com `validate_action_bundle.py` antes de entregar; ele recusa o que não prova.

## 7. Exemplo completo (bellwether, 2026-09-21)

- Sistema: `bellwether`, ponto de decisão `decision_contracts.resolve` (pergunta em linguagem
  natural → qual contrato de decisão e crop, ou recusa). Implementação anterior: keywords.
- Bundle: 5 ações, 5 estados, 1 superfície (`corn | soybeans | unsupported`), 18 evidências.
  Gates determinísticos (registry, contrato vencido, ano histórico) viraram precondições.
- Adapter gerado: 5 handlers reais (`_render`, `_limitation`, `audit`), 0 stubs.
- Encaixe: `bellwether/decision_router.py` (150 linhas: shim de estado + fallback + oráculo) e
  1 linha em `serve.decision_context`. Flag `BELLWETHER_JEV_ROUTING=1`.
- Resultado em 55 perguntas rotuladas: keywords 33/55; JEV 53/54 (o erro com confiança 0,37,
  abstido pela política). Held-out: 0 ações ilegais, boundary 11/11, abstenção 4/4.
  `RELEASE GATE: APPROVE`, aprovado pelo dono.

## 8. Perguntas frequentes

**O Forge injeta o JEV em qualquer sistema?**
Não sozinho. Ele produz, para qualquer sistema, o bundle e o adapter; o sistema precisa chamar o
adapter em um ponto e fornecer o estado observável. Esse encaixe é pequeno, determinístico e
igual para todos os sistemas, mas é uma mudança no sistema. "Qualquer sistema" vale para o que
tem: saídas finitas e nomeáveis, escolha semântica, estado observável por código, execução
observável. Websites e ferramentas MCP encaixam no modelo: bindings `ui` viram handlers Playwright
(abrir URL, clicar, preencher, ler um seletor) e bindings `mcp` viram chamadas `tools/call` via
SDK do MCP (stdio ou HTTP), desde que a invocação tenha sido observada na descoberta.

**O JEV pode executar algo errado?**
Pode escolher errado; não pode executar errado. A escolha passa por: conjunto legal, threshold
calibrado, precondições reavaliadas, confirmação para ações irreversíveis, handler que só existe
com evidência. Um erro do JEV vira abstenção ou fallback, e fica no log.

**E quando as opções só existem em runtime (candidatos de busca, botões de uma página)?**
`criteria_source: dynamic`: o bundle fixa o tipo de ação e a abstenção; o host passa as opções
concretas em `dynamic_choices`; qualquer escolha dinâmica mapeia para `dynamic_executor_action_id`.

**De onde vêm os thresholds?**
Do log rotulado: menor bin de confiança cuja acurácia acumulada fica ≥ 0,97, por pergunta e por
ação, com mínimo de amostras. Sem histórico, tudo abstém.

**O que sobra para o humano?**
Credenciais, aprovar o gate, revisar rótulos, resolver abstenções. Nada de stubs, thresholds ou
scripts de eval.

---

Resumo para quem só lê uma linha: o Forge não coloca o JEV dentro do sistema; ele produz um
adapter gerado, verificado e calibrado que o sistema chama num único ponto, e a partir daí o
JEV decide só o que é semântico, o código decide todo o resto, e o humano decide se libera.
