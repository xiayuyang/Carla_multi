from collections import deque

# test deque, is iterable
test_deque = deque(maxlen=10)
test_deque.append('1')
test_deque.append('2')
for i in test_deque:
    print(i)

# test [1,2,3] split
test_list = [1, 2, 3]
a1, a2, a3 = test_list
print(a1, a2, a3)