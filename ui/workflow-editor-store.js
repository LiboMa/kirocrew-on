/* Browser drafts belong to one Session or one explicitly created draft. */
const WorkflowEditorStore = (() => {
  const prefix = "kirocrew.workflow.editor.v3:";
  const clone = value => JSON.parse(JSON.stringify(value));
  class Store {
    constructor(storage) { this.storage = storage; this.memory = new Map(); }
    read(scope) {
      let value;
      try { value = JSON.parse(this.storage.getItem(prefix + scope) || "null") || this.memory.get(scope); }
      catch { value = this.memory.get(scope); }
      return value?.schema === 3 && value.scope === scope ? clone(value) : null;
    }
    write(scope, value) {
      if (!/^(run|flow|draft):[a-zA-Z0-9-]+$/.test(scope)) throw Error("无效的编辑上下文。");
      const record = {...clone(value), schema:3, scope};
      this.memory.set(scope, record);
      this.storage.setItem(prefix + scope, JSON.stringify(record));
    }
    list() {
      const scopes = new Set(this.memory.keys());
      try {
        for (let i = 0; i < this.storage.length; i++) {
          const key = this.storage.key(i);
          if (key?.startsWith(prefix)) scopes.add(key.slice(prefix.length));
        }
      } catch { /* In-memory drafts remain available in this tab. */ }
      return [...scopes].map(scope => this.read(scope)).filter(Boolean);
    }
    remove(scope) { this.memory.delete(scope); this.storage.removeItem(prefix + scope); }
    updateGeneration(scope, id, patch) {
      const record = this.read(scope);
      if (record?.generation?.id !== id) return false;
      record.generation = {...record.generation, ...clone(patch)};
      this.write(scope, record);
      return true;
    }
    migrate(legacy, definitions) {
      // An old global editor had no reliable Session owner. Recover it as
      // an independent draft; never assign its input to a native Session.
      const marker = prefix + "legacy-migrated";
      if (this.storage.getItem(marker)) return;
      const build = legacy?.build || {}, activeId = build.workflow_id;
      const drafts = {...legacy?.drafts};
      const active = drafts[activeId] || definitions.find(d => d.id === activeId);
      if (active && (build.active || build.input || build.intent || legacy.generation)) drafts[activeId] = active;
      for (const [id, config] of Object.entries(drafts)) {
        if (!config?.steps || !/^[a-zA-Z0-9-]+$/.test(id)) continue;
        const scope = "draft:legacy-" + id;
        if (this.read(scope)) continue;
        const isActive = id === activeId;
        this.write(scope, {config, drafts:{[id]:config}, dirty:!!legacy.drafts?.[id],
          input:isActive ? build.input || build.intent || "" : "", inputTouched:true,
          suggestedInput:isActive ? build.suggestedInput || "" : "",
          chat:isActive && !!build.chat, stage:isActive ? build.stage || 0 : 0,
          selected:0, active:true, recovered:true,
          generation:isActive ? legacy.generation || null : null,
          requests:isActive ? legacy.requests || [] : []});
      }
      this.storage.setItem(marker, "1");
    }
  }
  return {Store, prefix};
})();
if (typeof module !== "undefined") module.exports = WorkflowEditorStore;
