// SPDX-License-Identifier: Apache-2.0
enum Color { Red=3, Blue=5 }
function value(c: Color): number { return c+1; }
print(value(Color.Blue));
