from collections import deque
import numpy as np
import torch
# test deque, is iterable
# test_deque = deque(maxlen=10)
# test_deque.append('1')
# test_deque.append('2')
# for i in test_deque:
#     print(i)

# test [1,2,3] split
# test_list = [1, 2, 3]
# a1, a2, a3 = test_list
# print(a1, a2, a3)
# 1 2 3

# test cumsum() and insert
# action_parameter_sizes = np.array([2, 2, 2])
# action_parameter_size = int(action_parameter_sizes.sum())
# action_parameter_offsets = action_parameter_sizes.cumsum()
# action_parameter_offsets = np.insert(action_parameter_offsets, 0, 0)
# print(action_parameter_offsets)
# [0 2 4 6]

# test torch.max(pred_Q_a, 1, keepdim=True)[0].squeeze()
# pred_Q_a = torch.tensor([[1,2,3],[4,5,6]])
# print(torch.max(pred_Q_a, 1, keepdim=True)[0].squeeze())

# test torch.tensor, only works in tensor
# batch_action_indices = np.array([[1], [2], [3], [1], [2], [3], [1], [2]])
# batch_action_indices = torch.from_numpy(batch_action_indices)
# grad = np.ones((8, 6))
# # print(grad, grad>0)
# action_parameter_offsets = [0, 2, 4, 6]
# ind = torch.zeros(6, dtype=torch.long)
# for a in range(3):
#     ind[action_parameter_offsets[a]:action_parameter_offsets[a + 1]] = a
# # ind_tile = np.tile(ind, (self.batch_size, 1))
# ind_tile = ind.repeat(8, 1)
# print(ind_tile)
# # print(batch_action_indices[:, np.newaxis])
# # batch_action_indices_tile = batch_action_indices.repeat(6, 1)
# actual_index = (ind_tile != batch_action_indices)
# print(actual_index)
# grad[actual_index]=0.0
# print(grad)

# test argmax
# q = [1.2, 2.3, 4.5]
# action = np.argmax(q)
# print(action)


# a=[None for i in range(4)]
# print(a)
# a=0.5
# for i in range(20000):
#     a=a*0.9999
# print(a)
# a=abs(0.01)
# print(a)
import torch
# a=torch.tensor(np.array([[1,2,3],[4,5,6]]), dtype=torch.float32)
# b=torch.tensor(np.array([[0, 1]]), dtype=torch.int64)
# c=torch.tensor(np.array([[0],[1]]), dtype=torch.int64)
# print(a.gather(1, b), a.gather(1, c))
# d = np.array([[0], [1]])
# e = d[:, np.newaxis]
# print(d, e)
ind = torch.zeros(6, dtype=torch.long)
ind[0:2] = 0
ind[2:4] = 1
ind[4:6] = 2
ind_tile = ind.repeat(4, 1)
a = torch.tensor(np.array([[1], [0], [2], [0]]), dtype=torch.int64)
print(ind_tile != a)
print(ind_tile)
# actual_index = ind_tile != batch_action_indices
