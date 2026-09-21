import { readFileSync } from "node:fs";
const src = readFileSync("web/app.js", "utf8");
// Pull the two pure functions out and exercise them.
const grab = (name) => {
  const i = src.indexOf(`function ${name}(`);
  let depth = 0, j = src.indexOf("{", i);
  for (let k = j; k < src.length; k++) {
    if (src[k] === "{") depth++;
    else if (src[k] === "}") { depth--; if (!depth) { j = k; break; } }
  }
  return src.slice(i, j + 1);
};
const openSpec = () => ({ people: [{ id: "p_cfo", name: "Marcus Oyelaran",
                                     contact: "m@x.example" }] });
const csv = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);
const fn = new Function("openSpec", "csv",
  grab("personOf") + grab("personLabel") + grab("humanContact") +
  grab("humanToLine") + grab("parseHumans") +
  "return { humanToLine, parseHumans, personLabel };")(openSpec, csv);

const cases = [
  { person: "p_cfo", roles: ["owner"], approves: [] },
  { person: "p_ctrl", roles: ["approver"], approves: ["pay"] },
  { name: "Inline Person", contact: "i@x.example", roles: ["owner"], approves: [] },
];
let ok = true;
for (const original of cases) {
  const line = fn.humanToLine(original);
  const back = fn.parseHumans(line)[0];
  const same = JSON.stringify({ ...original }) === JSON.stringify({
    ...(original.person ? { person: back.person } : { name: back.name, contact: back.contact }),
    roles: back.roles, approves: back.approves,
  });
  console.log(same ? "OK  " : "LOSS", JSON.stringify(line), "->", JSON.stringify(back));
  ok &&= same;
}
console.log("label for a reference:", fn.personLabel(cases[0]));
console.log("label for an unknown reference:", fn.personLabel({ person: "p_ghost" }));
process.exit(ok ? 0 : 1);
