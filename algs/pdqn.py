import random, collections
import numpy as np
import torch
from torch import nn
import torch.nn.functional as F
from torch.autograd import Variable


class ReplayBuffer:
    """经验回放池"""

    def __init__(self, capacity) -> None:
        self.buffer = collections.deque(maxlen=capacity)  # 队列，先进先出
        self.number = 0
        # self.all_buffer = np.zeros((1000000, 66), dtype=np.float32)
        with open('./out/replay_buffer_test.txt', 'w') as f:
            pass

    def add(self, state, action, action_param, reward, next_state, truncated, done, info):
        # first compress state info, then add
        state = self._compress(state)
        next_state = self._compress(next_state)
        self.buffer.append((state, action, action_param, reward, next_state, truncated, done))
        # reward_ttc = info["TTC"]
        # reward_com = info["Comfort"]
        # reward_eff = info["velocity"]
        # reward_lan = info["offlane"]
        # reward_yaw = info["yaw_diff"]


    def sample(self, batch_size):  # 从buffer中采样数据,数量为batch_size
        transition = random.sample(self.buffer, batch_size)
        state, action, action_param, reward, next_state, truncated, done = zip(*transition)
        return state, action, action_param, reward, next_state, truncated, done

    def size(self):
        return len(self.buffer)

    def _compress(self, state):
        # print('state: ', state)
        state_left_wps = np.array(state['left_waypoints'], dtype=np.float32).reshape((1, -1))
        state_center_wps = np.array(state['center_waypoints'], dtype=np.float32).reshape((1, -1))
        state_right_wps = np.array(state['right_waypoints'], dtype=np.float32).reshape((1, -1))
        state_veh_left_front = np.array(state['vehicle_info'][0], dtype=np.float32).reshape((1, -1))
        state_veh_front = np.array(state['vehicle_info'][1], dtype=np.float32).reshape((1, -1))
        state_veh_right_front = np.array(state['vehicle_info'][2], dtype=np.float32).reshape((1, -1))
        state_veh_left_rear = np.array(state['vehicle_info'][3], dtype=np.float32).reshape((1, -1))
        state_veh_rear = np.array(state['vehicle_info'][4], dtype=np.float32).reshape((1, -1))
        state_veh_right_rear = np.array(state['vehicle_info'][5], dtype=np.float32).reshape((1, -1))
        state_ev = np.array(state['ego_vehicle'], dtype=np.float32).reshape((1, -1))

        state_ = np.concatenate((state_left_wps, state_veh_left_front, state_veh_left_rear,
                                 state_center_wps, state_veh_front, state_veh_rear,
                                 state_right_wps, state_veh_right_front, state_veh_right_rear, state_ev), axis=1)
        return state_


class veh_lane_encoder(torch.nn.Module):
    def __init__(self, state_dim, train=True):
        super().__init__()
        self.state_dim = state_dim
        self.train = train
        self.lane_encoder = nn.Linear(state_dim['waypoints'], 32)
        self.veh_encoder = nn.Linear(state_dim['conventional_vehicle'] * 2, 32)
        self.agg = nn.Linear(64, 64)

    def forward(self, lane_veh):
        lane = lane_veh[:, :self.state_dim["waypoints"]]
        veh = lane_veh[:, self.state_dim["waypoints"]:]
        lane_enc = F.relu(self.lane_encoder(lane))
        veh_enc = F.relu(self.veh_encoder(veh))
        state_cat = torch.cat((lane_enc, veh_enc), dim=1)
        state_enc = F.relu(self.agg(state_cat))
        return state_enc


class PolicyNet_multi(torch.nn.Module):
    def __init__(self, state_dim, action_parameter_size, action_bound, train=True) -> None:
        # the action bound and state_dim here are dicts
        super().__init__()
        self.state_dim = state_dim
        self.action_bound = action_bound
        self.action_parameter_size = action_parameter_size
        self.train = train
        self.left_encoder = veh_lane_encoder(self.state_dim)
        self.center_encoder = veh_lane_encoder(self.state_dim)
        self.right_encoder = veh_lane_encoder(self.state_dim)
        self.ego_encoder = nn.Linear(self.state_dim['ego_vehicle'], 64)
        self.fc = nn.Linear(256, 256)
        self.fc_out = nn.Linear(256, self.action_parameter_size)
        # torch.nn.init.normal_(self.fc1_1.weight.data,0,0.01)
        # torch.nn.init.normal_(self.fc1_2.weight.data,0,0.01)
        # torch.nn.init.normal_(self.fc_out.weight.data,0,0.01)
        # torch.nn.init.normal_(self.fc_out.weight.data,0,0.01)
        # torch.nn.init.xavier_normal_(self.fc1_1.weight.data)
        # torch.nn.init.xavier_normal_(self.fc1_2.weight.data)
        # torch.nn.init.xavier_normal_(self.fc_out.weight.data)

    def forward(self, state):
        # state: (waypoints + 2 * conventional_vehicle0 * 3
        one_state_dim = self.state_dim['waypoints'] + self.state_dim['conventional_vehicle'] * 2
        left_enc = self.left_encoder(state[:, :one_state_dim])
        center_enc = self.center_encoder(state[:, one_state_dim:2*one_state_dim])
        right_enc = self.right_encoder(state[:, 2*one_state_dim:3*one_state_dim])
        ego_enc = self.ego_encoder(state[:, 3*one_state_dim:])
        state_ = torch.cat((left_enc, center_enc, right_enc, ego_enc), dim=1)
        hidden = F.relu(self.fc(state_))
        action = torch.tanh(self.fc_out(hidden))
        # steer,throttle_brake=torch.split(out,split_size_or_sections=[1,1],dim=1)
        # steer=steer.clone()
        # throttle_brake=throttle_brake.clone()
        # steer*=self.action_bound['steer']
        # throttle=throttle_brake.clone()
        # brake=throttle_brake.clone()
        # for i in range(throttle.shape[0]):
        #     if throttle[i][0]<0:
        #         throttle[i][0]=0
        #     if brake[i][0]>0:
        #         brake[i][0]=0
        # throttle*=self.action_bound['throttle']
        # brake*=self.action_bound['brake']

        return action


class QValueNet_multi(torch.nn.Module):
    def __init__(self, state_dim, action_param_dim, num_actions) -> None:
        # parameter state_dim here is a dict
        super().__init__()
        self.state_dim = state_dim
        self.action_param_dim = action_param_dim
        self.num_actions = num_actions
        self.left_encoder = veh_lane_encoder(self.state_dim)
        self.center_encoder = veh_lane_encoder(self.state_dim)
        self.right_encoder = veh_lane_encoder(self.state_dim)
        self.ego_encoder = nn.Linear(self.state_dim['ego_vehicle'], 32)
        self.action_encoder = nn.Linear(self.action_param_dim, 32)
        self.fc = nn.Linear(256, 256)
        self.fc_out = nn.Linear(256, self.num_actions)

        # torch.nn.init.normal_(self.fc1.weight.data,0,0.01)
        # torch.nn.init.normal_(self.fc_out.weight.data,0,0.01)
        # torch.nn.init.xavier_normal_(self.fc1.weight.data)
        # torch.nn.init.xavier_normal_(self.fc_out.weight.data)

    def forward(self, state, action):
        one_state_dim = self.state_dim['waypoints'] + self.state_dim['conventional_vehicle'] * 2
        left_enc = self.left_encoder(state[:, :one_state_dim])
        center_enc = self.center_encoder(state[:, one_state_dim:2*one_state_dim])
        right_enc = self.right_encoder(state[:, 2*one_state_dim:3*one_state_dim])
        ego_enc = self.ego_encoder(state[:, 3*one_state_dim:])
        action_enc = self.action_encoder(action)
        state_ = torch.cat((left_enc, center_enc, right_enc, ego_enc, action_enc), dim=1)
        hidden = F.relu(self.fc(state_))
        out = self.fc_out(hidden)
        return out


class P_DQN:
    def __init__(self, state_dim, action_dim, action_bound, gamma, tau, sigma, theta, epsilon,
                 buffer_size, batch_size, actor_lr, critic_lr, clip_grad, zero_index_gradients, inverting_gradients, device) -> None:
        self.learn_time = 0
        self.replace_a = 0
        self.replace_c = 0
        self.s_dim = state_dim  # state_dim here is a dict
        self.s_dim['waypoints'] *= 2  # 2 is the feature dim of each waypoint
        self.a_dim, self.a_bound = action_dim, action_bound
        self.theta = theta
        self.num_actions = 3  # left change, lane follow, right change
        self.action_parameter_sizes = np.array([self.a_dim, self.a_dim, self.a_dim])
        self.action_parameter_size = int(self.action_parameter_sizes.sum())
        self.action_parameter_offsets = self.action_parameter_sizes.cumsum()
        self.action_parameter_offsets = np.insert(self.action_parameter_offsets, 0, 0)  # [0, self.a_dim, self.a_dim*2, self.a_dim*3]
        self.action_parameter_max_numpy = [1, 1, 1]
        self.action_parameter_min_numpy = [-1, -1, -1]
        self.action_parameter_range_numpy = [2, 2, 2]
        self.gamma, self.tau, self.sigma, self.epsilon = gamma, tau, sigma, epsilon  # sigma:高斯噪声的标准差，均值直接设置为0
        self.buffer_size, self.batch_size, self.device = buffer_size, batch_size, device
        self.actor_lr, self.critic_lr = actor_lr, critic_lr
        self.clip_grad = clip_grad
        self.indexd = False
        self.zero_index_gradients = zero_index_gradients
        self.inverting_gradients = inverting_gradients
        # adjust different types of replay buffer
        #self.replay_buffer = Split_ReplayBuffer(buffer_size)
        self.replay_buffer = ReplayBuffer(buffer_size)
        # self.replay_buffer = offline_replay_buffer()
        """self.memory=torch.tensor((buffer_size,self.s_dim*2+self.a_dim+1+1),
            dtype=torch.float32).to(self.device)"""
        self.pointer = 0  # serve as updating the memory data
        self.train = True
        self.actor = PolicyNet_multi(self.s_dim, self.action_parameter_size, self.a_bound).to(self.device)
        self.actor_target = PolicyNet_multi(self.s_dim, self.action_parameter_size, self.a_bound).to(self.device)
        self.actor_target.load_state_dict(self.actor.state_dict())
        self.critic = QValueNet_multi(self.s_dim, self.action_parameter_size, self.num_actions).to(self.device)
        self.critic_target = QValueNet_multi(self.s_dim, self.action_parameter_size, self.num_actions).to(self.device)
        # self.actor = PolicyNet(self.s_dim, self.a_bound).to(self.device)
        # self.actor_target = PolicyNet(self.s_dim, self.a_bound).to(self.device)
        # self.actor_target.load_state_dict(self.actor.state_dict())
        # self.critic = QValueNet(self.s_dim, self.a_dim).to(self.device)
        # self.critic_target = QValueNet(self.s_dim, self.a_dim).to(self.device)
        self.critic_target.load_state_dict(self.critic.state_dict())

        self.actor_optimizer = torch.optim.Adam(self.actor.parameters(), lr=actor_lr)
        self.critic_optimizer = torch.optim.Adam(self.critic.parameters(), lr=critic_lr)
        self.loss = nn.MSELoss()

        # self.steer_noise = OrnsteinUhlenbeckActionNoise(self.sigma, self.theta)
        # self.tb_noise = OrnsteinUhlenbeckActionNoise(self.sigma, self.theta)

    def take_action(self, state):
        # TODO: return action and action_param
        # print('vehicle_info', state['vehicle_info'])
        state_left_wps = torch.tensor(state['left_waypoints'], dtype=torch.float32).view(1, -1).to(self.device)
        state_center_wps = torch.tensor(state['center_waypoints'], dtype=torch.float32).view(1, -1).to(self.device)
        state_right_wps = torch.tensor(state['right_waypoints'], dtype=torch.float32).view(1, -1).to(self.device)
        state_veh_left_front = torch.tensor(state['vehicle_info'][0], dtype=torch.float32).view(1, -1).to(self.device)
        state_veh_front = torch.tensor(state['vehicle_info'][1], dtype=torch.float32).view(1, -1).to(self.device)
        state_veh_right_front = torch.tensor(state['vehicle_info'][2], dtype=torch.float32).view(1, -1).to(self.device)
        state_veh_left_rear = torch.tensor(state['vehicle_info'][3], dtype=torch.float32).view(1, -1).to(self.device)
        state_veh_rear = torch.tensor(state['vehicle_info'][4], dtype=torch.float32).view(1, -1).to(self.device)
        state_veh_right_rear = torch.tensor(state['vehicle_info'][5], dtype=torch.float32).view(1, -1).to(self.device)
        state_ev = torch.tensor(state['ego_vehicle'],dtype=torch.float32).view(1,-1).to(self.device)
        state_ = torch.cat((state_left_wps, state_veh_left_front, state_veh_left_rear,
                            state_center_wps, state_veh_front, state_veh_rear,
                            state_right_wps, state_veh_right_front, state_veh_right_rear, state_ev), dim=1)
        # print(state_.shape)
        all_action_param = self.actor(state_)
        q_a = self.critic(state_, all_action_param).unsqueeze(0)
        q_a = q_a.detach().cpu().numpy()
        action = np.argmax(q_a)
        action_param = all_action_param[:, self.action_parameter_offsets[action]:self.action_parameter_offsets[action+1]]

        print(f'Network Output - Steer: {action_param[0][0]}, Throttle_brake: {action_param[0][1]}')
        if (action_param[0, 0].is_cuda):
            action_param = np.array([action_param[:, 0].detach().cpu().numpy(), action_param[:, 1].detach().cpu().numpy()]).reshape((-1, 2))
        else:
            action_param = np.array([action_param[:, 0].detach().numpy(), action_param[:, 1].detach().numpy()]).reshape((-1, 2))
        # if np.random.random()<self.epsilon:
        if self.train:
            action_param[:, 0] = np.clip(np.random.normal(action_param[:, 0], self.sigma), -1, 1)
            action_param[:, 1] = np.clip(np.random.normal(action_param[:, 1], self.sigma), -1, 1)
        # if self.train:
        #     action[:,0]=np.clip(action[:,0]+self.steer_noise(),-1,1)
        #     action[:,1]=np.clip(action[:,1]+self.tb_noise(),-1,1)
        print(f'After noise - Steer: {action_param[0][0]}, Throttle_brake: {action_param[0][1]}')
        # for i in range(action.shape[0]):
        #     if action[i,1]>0:
        #         action[i,1]+=np.clip(np.random.normal(action[i,1],self.sigma),0,self.a_bound['throttle'])
        #     elif action[i,2]<0:
        #         action[i,2]+=np.clip(np.random.normal(action[i,2],self.sigma),-self.a_bound['brake'],0)

        return action, action_param, all_action_param

    def _zero_index_gradients(self, grad, batch_action_indices, inplace=True):
        assert grad.shape[0] == batch_action_indices.shape[0]
        grad = grad.cpu()

        if not inplace:
            grad = grad.clone()
        with torch.no_grad():
            ind = torch.zeros(self.action_parameter_size, dtype=torch.long)
            for a in range(self.num_actions):
                ind[self.action_parameter_offsets[a]:self.action_parameter_offsets[a+1]] = a
            # ind_tile = np.tile(ind, (self.batch_size, 1))
            ind_tile = ind.repeat(self.batch_size, 1).to(self.device)
            actual_index = ind_tile != batch_action_indices
            # print('actual_index: ', actual_index)
            grad[actual_index] = 0.
        return grad

    def _invert_gradients(self, grad, vals, grad_type, inplace=True):
        if grad_type == "action_parameters":
            max_p = self.action_parameter_max_numpy
            min_p = self.action_parameter_min_numpy
            rnge = self.action_parameter_range_numpy
        else:
            raise ValueError("Unhandled grad_type: '"+str(grad_type) + "'")

        max_p = max_p.cpu()
        min_p = min_p.cpu()
        rnge = rnge.cpu()
        grad = grad.cpu()
        vals = vals.cpu()

        assert grad.shape == vals.shape

        if not inplace:
            grad = grad.clone()
        with torch.no_grad():
            # index = grad < 0  # actually > but Adam minimises, so reversed (could also double negate the grad)
            index = grad > 0
            grad[index] *= (index.float() * (max_p - vals) / rnge)[index]
            grad[~index] *= ((~index).float() * (vals - min_p) / rnge)[~index]

        return grad

    def learn(self):
        self.learn_time += 1
        # if self.learn_time > 100000:
        #     self.train = False
        self.replace_a += 1
        self.replace_c += 1
        b_s, b_a, b_a_param, b_r, b_ns, b_t, b_d = self.replay_buffer.sample(self.batch_size)
        # 此处得到的batch是否是pytorch.tensor?
        batch_s = torch.tensor(b_s, dtype=torch.float32).view((self.batch_size, -1)).to(self.device)
        batch_ns = torch.tensor(b_ns, dtype=torch.float32).view((self.batch_size, -1)).to(self.device)
        batch_a = torch.tensor(b_a, dtype=torch.float32).view((self.batch_size, -1)).to(self.device)
        batch_a_param = torch.tensor(b_a_param, dtype=torch.float32).view((self.batch_size, -1)).to(self.device)
        batch_r = torch.tensor(b_r, dtype=torch.float32).view((self.batch_size, -1)).to(self.device)
        batch_d = torch.tensor(b_d, dtype=torch.float32).view((self.batch_size, -1)).to(self.device)
        batch_t = torch.tensor(b_t, dtype=torch.float32).view((self.batch_size, -1)).to(self.device)

        with torch.no_grad():
            action_param_target = self.actor_target(batch_ns)
            q_target_values = self.critic_target(batch_ns, action_param_target)
            q_prime = torch.max(q_target_values, 1, keepdim=True)[0].squeeze()
            q_targets = batch_r + self.gamma * q_prime * (1 - batch_t)

        q_values = self.critic(batch_s, batch_a_param)
        q = q_values.gather(1, batch_a.view(-1, 1)).squeeze()
        loss_q = self.loss(q, q_targets)

        self.critic_optimizer.zero_grad()
        loss_q.backward()
        if self.clip_grad > 0:
            torch.nn.utils.clip_grad_norm_(self.critic.parameters(), self.clip_grad)
        self.critic_optimizer.step()

        with torch.no_grad():
            action_param = self.actor(batch_s)
        action_param.requires_grad = True
        Q = self.critic(batch_s, action_param)
        Q_val = Q
        if self.indexd:
            Q_indexed = Q_val.gather(1, batch_a.unsqueeze(1))
            Q_loss = torch.mean(Q_indexed)
        else:
            Q_loss = torch.mean(torch.sum(Q_val, 1))

        self.critic.zero_grad()
        Q_loss.backward()
        from copy import deepcopy
        print('check batch_s whether has grad: ', batch_s.grad_fn)
        delta_a = deepcopy(action_param.grad.data)

        action_param = self.actor(Variable(batch_s))
        delta_a[:] = self._invert_gradients(delta_a, action_param, grad_type="action_parameters", inplace=True)
        if self.zero_index_gradients:
            delta_a[:] = self._zero_index_gradients(delta_a, batch_action_indices=batch_a, inplace=True)

        out = -torch.mul(delta_a, action_param)
        self.actor.zero_grad()
        out.backward(torch.ones(out.shape).to(self.device))
        if self.clip_grad > 0:
            torch.nn.utils.clip_grad_norm_(self.actor.parameters(), self.clip_grad)
        self.actor_optimizer.step()
        self.soft_update(self.actor, self.actor_target)
        self.soft_update(self.critic, self.critic_target)

    def _print_grad(self, model):
        '''Print the grad of each layer'''
        for name, parms in model.named_parameters():
            print('-->name:', name, '-->grad_requirs:', parms.requires_grad, ' -->grad_value:', parms.grad)

    def set_sigma(self, sigma):
        self.sigma = sigma
        # self.steer_noise.set_sigma(sigma)
        # self.tb_noise.set_sigma(sigma)

    def reset_noise(self):
        pass
        # self.steer_noise.reset()
        # self.tb_noise.reset()

    def soft_update(self, net, target_net):
        for param_target, param in zip(target_net.parameters(), net.parameters()):
            param_target.data.copy_(param_target.data * (1.0 - self.tau) + param.data * self.tau)

    def hard_update(self, net, target_net):
        net.load_state_dict(target_net.state_dict())

    def store_transition(self, transition_dict):  # how to store the episodic data to buffer
        index = self.pointer % self.buffer_size
        states = torch.tensor(transition_dict['states'],
                              dtype=torch.float32).view(-1, 1).to(self.device)
        actions = torch.tensor(transition_dict['actions'],
                               dtype=torch.float32).to(self.device)
        rewards = torch.tensor(transition_dict['rewards'],
                               dtype=torch.float32).to(self.device)
        states_next = torch.tensor(transition_dict['states_next'],
                                   dtype=torch.float32).view(-1, 1).to(self.device)
        dones = torch.tensor(transition_dict['dones'],
                             dtype=torch.float32).to(self.device)
        return

    def save_net(self,file='./out/ddpg_final.pth'):
        state = {
            'actor': self.actor.state_dict(),
            'actor_target':self.actor_target.state_dict(),
            'critic': self.critic.state_dict(),
            'critic_target':self.critic_target.state_dict(),
            'actor_optimizer': self.actor_optimizer.state_dict(),
            'critic_optimizer': self.critic_optimizer.state_dict()
        }
        torch.save(state, file)

    def load_net(self, state):
        self.critic.load_state_dict(state['critic'])
        self.critic_target.load_state_dict(state['critic_target'])
        self.actor.load_state_dict(state['actor'])
        self.actor_target.load_state_dict(state['actor_target'])
        self.actor_optimizer.load_state_dict(state['actor_optimizer'])
        self.critic_optimizer.load_state_dict(state['critic_optimizer'])







