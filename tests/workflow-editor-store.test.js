const {test} = require("node:test");
const assert = require("node:assert/strict");
const {Store} = require("../ui/workflow-editor-store.js");

class Storage {
  data = new Map();
  get length() { return this.data.size; }
  key(i) { return [...this.data.keys()][i]; }
  getItem(k) { return this.data.get(k) ?? null; }
  setItem(k,v) { this.data.set(k,v); }
  removeItem(k) { this.data.delete(k); }
}
const definition = {id:"development",revision:1,name:"Development",steps:[{id:"code"}]};

test("two Sessions sharing one workflow keep independent goals, config and requests", () => {
  const store = new Store(new Storage());
  store.write("run:session-a", {config:definition,input:"A",requests:[["same-input","request-a"]]});
  store.write("run:session-b", {config:definition,input:"B",requests:[["same-input","request-b"]]});
  const a = store.read("run:session-a");
  a.input = "A edited";a.config.steps[0].id = "review";
  store.write("run:session-a", a);
  assert.equal(store.read("run:session-b").input, "B");
  assert.equal(store.read("run:session-b").config.steps[0].id, "code");
  assert.equal(store.read("run:session-b").requests[0][1], "request-b");
});
test("late generation responses only update their original Session and current job", () => {
  const store = new Store(new Storage());
  store.write("run:session-a", {generation:{id:"job-2",intent:"A"},input:"A edited"});
  store.write("run:session-b", {generation:{id:"job-b",intent:"B"},input:"B"});
  assert.equal(store.updateGeneration("run:session-a","job-1",{status:"ready"}),false);
  assert.equal(store.updateGeneration("run:session-a","job-2",{status:"ready"}),true);
  assert.equal(store.read("run:session-a").input,"A edited");
  assert.equal(store.read("run:session-b").generation.status,undefined);
});
test("separate tabs saving different Sessions do not overwrite each other's records", () => {
  const storage = new Storage(), left = new Store(storage), right = new Store(storage);
  left.write("run:session-a",{input:"A"});
  right.write("run:session-b",{input:"B"});
  left.write("run:session-a",{input:"A updated"});
  assert.equal(new Store(storage).read("run:session-b").input,"B");
  assert.equal(right.read("run:session-a").input,"A updated");
});
test("legacy global data is recovered once as a separate draft, never assigned to a Session", () => {
  const storage = new Storage(), store = new Store(storage);
  const legacy = {build:{workflow_id:"development",input:"old input",active:true},
    generation:{id:"old-job"},drafts:{development:definition}};
  const before = JSON.stringify(legacy);
  store.write("run:session-a",{input:"A"});
  store.migrate(legacy,[definition]);
  assert.equal(store.read("run:session-a").input,"A");
  assert.equal(store.read("draft:legacy-development").input,"old input");
  assert.equal(JSON.stringify(legacy),before);
  store.remove("draft:legacy-development");
  new Store(storage).migrate(legacy,[definition]);
  assert.equal(store.read("draft:legacy-development"),null);
});
test("failed browser writes retain an in-memory copy and reject invalid scopes", () => {
  const storage = new Storage(), store = new Store(storage);
  storage.setItem = () => {throw Error("quota");};
  assert.throws(()=>store.write("run:session-a",{input:"keep"}),/quota/);
  assert.equal(store.read("run:session-a").input,"keep");
  assert.throws(()=>store.write("global",{input:"not scoped"}),/无效/);
});
