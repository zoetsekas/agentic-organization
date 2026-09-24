// Runs the Data and Deployment diagrams' own derivations from web/canvas.js
// (ADR-0111, ADR-0112 M7) over a spec, link rules and binding the Python
// test writes to a JSON file, and prints what they draw as JSON.
//
//   node tests/data_diagram_check.mjs <input.json>
import { readFileSync } from "node:fs";

const src = readFileSync("web/canvas.js", "utf8");

// The source of one top-level declaration: a function, or a `const` whose
// value is an object or array literal.
const grab = (name) => {
  let i = src.search(new RegExp(`^function ${name}\\(`, "m"));
  // A function's body opens at its first `) {`; a default like `= []` in
  // its parameters is not the body.
  let open = i >= 0 ? src.indexOf(") {", i) + 2 : -1;
  if (i < 0) {
    i = src.search(new RegExp(`^const ${name} =`, "m"));
    if (i < 0) throw new Error(`${name} is gone from canvas.js`);
    open = src.slice(i).search(/[{[]/) + i;
  }
  const close = src[open] === "{" ? "}" : "]";
  let depth = 0;
  for (let k = open; k < src.length; k++) {
    if (src[k] === src[open]) depth++;
    else if (src[k] === close && !--depth) {
      return src.slice(i, src[k + 1] === ";" ? k + 2 : k + 1);
    }
  }
  throw new Error(`${name} does not close`);
};

const input = JSON.parse(readFileSync(process.argv[2], "utf8"));
const canvas = { palette: { links: input.links }, record: { binding: input.binding } };
const spec = () => input.spec;
const el = () => null;

const names = ["SCOPE_RANK", "DATA_NOTATION", "SCOPE_WORDS", "BINDING_KINDS",
               "org", "walkTeams", "allAgents", "dataClasses", "dataClassById",
               "dataAgents", "dataRelationRule", "freshness", "dataEdges",
               "dataLayoutEdges", "inheritedRestrictions", "bindingTargets",
               "buildDeploymentModel", "deploymentSubtitle"];
const api = new Function("canvas", "spec", "el",
  names.map(grab).join("\n") + `\nreturn { ${names.join(", ")} };`)(canvas, spec, el);

const model = api.buildDeploymentModel();
const out = {
  edges: api.dataEdges(),
  layoutEdges: api.dataLayoutEdges(),
  agents: api.dataAgents().map((a) => a.id),
  inherited: Object.fromEntries(api.dataClasses().map((d) =>
    [d.id, api.inheritedRestrictions(d.id)])),
  deployment: {
    nodes: [...model.nodes.values()].map((n) => ({
      id: n.id, kind: n.kind, parent: n.parent, subtitle: api.deploymentSubtitle(n) })),
    edges: model.edges,
  },
};
console.log(JSON.stringify(out));
