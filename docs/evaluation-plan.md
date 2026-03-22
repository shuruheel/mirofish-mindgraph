# Evaluation Plan: MindGraph Integration

**Date:** 2026-03-21

---

## 1. Historical Replay / Backtesting

### Scenario Selection
Run at least one existing MiroFish demo scenario through both legacy (Zep-only) and MindGraph-enhanced paths. Candidate scenarios based on the repository's demo materials:
- Wuhan University public opinion simulation (武大模拟)
- Dream of the Red Chamber character simulation (红楼梦)

### Comparison Metrics

| Metric | How to Measure |
|--------|---------------|
| Prediction quality | Qualitative: does the report identify the correct outcome trajectory? (If ground truth exists for the scenario) |
| Report richness | Count: number of distinct facts cited, number of agent quotes, number of cross-references |
| Report specificity | Qualitative: does the report make specific claims with evidence, or generic observations? |
| Agent belief coherence | Query MindGraph for contradictions within a single agent's beliefs — lower = more coherent |

### Procedure
1. Upload the same seed document
2. Build graph (Zep + MindGraph Reality projection)
3. Generate same agent profiles (deterministic seed if possible, otherwise same LLM prompt)
4. Run simulation with same parameters, same round count
5. Generate report: once with Zep-only tools, once with MindGraph tools added
6. Compare reports side-by-side

---

## 2. Legacy vs. MindGraph Comparison

### Quantitative Metrics

| Metric | Measurement Method | Target |
|--------|-------------------|--------|
| Latency per round | Time from round start to round end, averaged over 10+ rounds | MindGraph-enhanced < 2x legacy |
| Total simulation time | Wall clock from `start_simulation` to completion | < 2x legacy |
| Graph size (Zep) | Node count + edge count after simulation | No change from legacy |
| Graph size (MindGraph) | Node count via `/retrieve` statistics | < 1000 nodes for 10-agent, 10-round sim |
| API calls to MindGraph | Count from mindgraph_client logs | < 50 per 10-round simulation |
| Memory updater queue depth | Max queue size during simulation | < 100 (no backpressure) |
| Failed writes | Count of retry-exhausted batches | 0 |

### Qualitative Metrics

| Metric | Evaluation Method |
|--------|------------------|
| Report quality | Blind comparison by 2+ reviewers on 5-point scale |
| Fact density | Count of unique facts cited per report section |
| Argument structure | Does the report present claims with evidence and counterarguments? |
| Belief evolution narrative | Does the report describe how opinions changed over time? |
| Cross-agent comparison | Does the report contrast different agents' perspectives? |

---

## 3. Explainability Comparison

### Questions the MindGraph-Enhanced System Should Answer

These questions should be testable via the ReportAgent chat interface:

| Question | Zep-Only | MindGraph-Enhanced |
|----------|----------|-------------------|
| "Which agents changed their position on X and why?" | Cannot answer — no belief versioning | Should answer via `/evolve/history` on agent claims |
| "What is the strongest argument for the predicted outcome?" | Can find facts, cannot assess argument strength | Should answer via `/retrieve` with `weak_claims` (inverse: find strong ones) |
| "Where do agent beliefs contradict each other?" | Manual search, no structured contradictions | Should answer via `/retrieve` with `unresolved_contradictions` |
| "How did public opinion evolve over the 3-day simulation?" | Can find temporal edges, but manual reconstruction | Should answer via `/traverse` chain + `/evolve/history` |
| "What evidence supports Claim X?" | Text search for related facts | Should answer via claim → evidence edges in Epistemic layer |

### Scoring
- For each question, score 0 (cannot answer), 1 (partial answer), 2 (full answer with evidence)
- MindGraph-enhanced system should score at least 3 points higher than Zep-only across all questions

---

## 4. Regression Checks

### Functional Regression

| Test | Condition | Method |
|------|-----------|--------|
| Graph construction | Zep graph builds successfully with MindGraph enabled | Run full pipeline, verify graph_id returned |
| Graph construction fallback | Zep graph builds when MindGraph is unavailable | Disable MindGraph API, verify Zep-only build succeeds |
| Simulation preparation | Profiles and configs generated correctly | Compare output files to baseline |
| Simulation execution | OASIS runs, actions logged, status updates work | Run 5-round simulation, verify run_state.json |
| Memory updates | Zep receives same data as before | Compare Zep graph node/edge counts with and without MindGraph |
| Report generation | Report generated with all sections | Generate report, verify markdown output |
| Report chat | Chat interface responds correctly | Send 3 test messages, verify responses include tool usage |
| Frontend | All routes load, data displays correctly | Manual verification of all 6 routes |
| API endpoints | All existing endpoints return same schema | Automated comparison of response shapes |

### Performance Regression

| Metric | Baseline (Zep-only) | Threshold |
|--------|---------------------|-----------|
| Graph build time | Measure baseline | < 1.5x baseline |
| Simulation prepare time | Measure baseline | < 1.2x baseline |
| Simulation round time | Measure baseline | < 2x baseline (hard constraint from project prompt) |
| Report generation time | Measure baseline | < 1.5x baseline |
| API response time (non-async) | Measure baseline | < 1.2x baseline |

### Agent Behavior Comparison
- Agent behavior may change with improved memory (this is expected and desirable)
- Behavior should not **degrade**: agents should not become less coherent, less responsive, or produce less meaningful content
- Measure: average post length, action diversity (ratio of DO_NOTHING to active actions), sentiment consistency

---

## 5. Test Execution Schedule

| Phase | What to Test | When |
|-------|-------------|------|
| After Phase 1 (Foundation) | Event normalizer unit tests, client mock tests | Before proceeding to Phase 2 |
| After Phase 2 (Reality) | Reality projection integration test | Before proceeding to Phase 3 |
| After Phase 3 (Memory) | Session/trace integration test | Before proceeding to Phase 4 |
| After Phase 4 (Epistemic) | Claim writing integration test | Before proceeding to Phase 5 |
| After Phase 5 (Retrieval) | ReportAgent tool integration test | Before Phase 6 |
| Phase 6 (Dual-Write) | Full pipeline test, regression suite, performance benchmarks, report comparison | Dedicated evaluation phase |

---

## 6. Success Criteria

The integration is considered successful if:

1. **No regression** in existing simulation functionality (all regression checks pass)
2. **Latency constraint** met: simulation rounds < 2x slower with MindGraph enabled
3. **Report quality** improved: blind evaluation shows at least 1-point improvement on 5-point scale
4. **Explainability** improved: MindGraph-enhanced system scores at least 3 points higher on explainability questions
5. **Graceful degradation** works: system functions correctly when MindGraph is unavailable
6. **Feature flag** works: can toggle MindGraph on/off without restart or data loss
