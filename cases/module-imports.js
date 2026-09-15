// SPDX-License-Identifier: Apache-2.0
import value, { foo as renamed } from "./dependency";
import * as ns from "./other";
export { renamed };
export * from "./third";
export default function use() { return value + renamed() + ns.x; }
