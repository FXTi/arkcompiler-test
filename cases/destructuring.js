let [a, , b, ...rest] = [1, 2, 3, 4, 5];
let {x, y: z} = {x: 6, y: 7};
print(a + b + rest.length + x + z);
