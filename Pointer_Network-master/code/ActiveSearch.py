# -*- coding: utf-8 -*-
"""
Created on Wed Apr 17 21:11:27 2019

@author: zheng
"""
import torch

from Embedding_and_Release import get_hops_and_link_consumptions

Max_Jump = 6
Min_Jump = 2
INF = 9999999999


def active_search(ptr_net, s_nodes, s_links, v_nodes, v_links,
                  iter_time=300, batch_size=10,
                  lr_p=0.01, beta1=0.9, alpha=0.01, alpha_decay=0.9, current_node_utilization=0,
                  use_node_utilization=False, device='cpu'):
    '''
    searching for shortest node_mapping for a particular s_node_resource distribution
    s_nodes:(s_nodes_num),list
    v_nodes:(v_nodes_num),list
    s_links:(s_links_num),list,(node,node,bandwith)
    v_links:(v_links_num),list,(node,node,bandwith)

    return : result,0个或1个成功的映射方案（包括节点映射和链路映射）
    '''

    ptr_op = torch.optim.Adam(ptr_net.parameters(), lr=lr_p, betas=(beta1, 0.999))
    s_input = get_input(nodes=s_nodes, links=s_links)
    v_input = get_input(nodes=v_nodes, links=v_links)

    best_mapping_solution = {
        'embedding_success': False,
        'node_mapping_solution': [],
        'link_mapping_solution': dict(),
        'link_consumption': INF
    }
    baseline = -1

    # 搜索
    for i in range(iter_time):
        # shuffle input
        s_node_indexes, s_inputs = get_shuffled_indexes_and_inputs(input=s_input, batch_size=batch_size)

        # 使用ptrnet，对给定的输入，输出node mapping solutions
        node_mapping_solutions, shuffled_node_mapping_solutions, output_weights = ptr_net.get_node_mapping(
            s_node_indexes=s_node_indexes.to(device=device),
            s_inputs=s_inputs.to(device=device),
            v_input=v_input.to(device=device),
        )

        # 检测node mapping solutions是否符合，若符合则进行链路映射
        embedding_successes, link_mapping_solutions, link_consumptions, hops = get_hops_and_link_consumptions(
            s_nodes=s_nodes,
            s_links=s_links,
            v_nodes=v_nodes,
            v_links=v_links,
            node_mapping=node_mapping_solutions
        )

        # 记录下最优
        j = torch.argmin(link_consumptions)
        if link_consumptions[j] < best_mapping_solution['link_consumption']:
            best_mapping_solution['node_mapping_solution'] = node_mapping_solutions[j]
            best_mapping_solution['link_mapping_solution'] = link_mapping_solutions[j]
            best_mapping_solution['link_consumption'] = link_consumptions[j]
            best_mapping_solution['embedding_success'] = embedding_successes[j]

        if baseline == -1:
            baseline = link_consumptions.mean()

        # 计算loss
        adv = (baseline - link_consumptions).squeeze().to(device=device)
        cross_entropy_loss = ptr_net.get_CrossEntropyLoss(output_weights, shuffled_node_mapping_solutions)
        ptr_loss = torch.dot(cross_entropy_loss, adv)

        # ptr_loss = ptr_loss * (1.0 - current_node_utilization)
        # print('iter_time = ', i, '针对此虚拟网络请求，当前模型参数下预测的节点映射对应的baseline = ', baseline)

        # Adam优化参数
        ptr_net.zero_grad()
        ptr_loss.backward()
        ptr_op.step()

        # 更新滑动平均baseline
        baseline = baseline * alpha_decay + (1 - alpha_decay) * link_consumptions.mean()

    return {
        'ptr_net': ptr_net,
        'best_mapping_solution': best_mapping_solution
    }


# 给定nodes和links，生成网络的输入数据input
def get_input(nodes, links):
    node_num = len(nodes)
    node_resource = torch.Tensor(nodes).view(size=(node_num,))
    node_neighbour_link_resource_sum = torch.zeros(size=(node_num,))
    # node_neighbour_link_resource_min = torch.zeros(size=(node_num,))
    node_neighbour_link_resource_max = torch.ones(size=(node_num,)) * INF
    for link in links:
        u_node = link[0]
        v_node = link[1]
        bandwidth = link[2]
        node_neighbour_link_resource_sum[u_node] += bandwidth
        node_neighbour_link_resource_sum[v_node] += bandwidth
        # node_neighbour_link_resource_min[u_node] = min(node_neighbour_link_resource_min[u_node], bandwidth)
        # node_neighbour_link_resource_min[v_node] = min(node_neighbour_link_resource_min[v_node], bandwidth)
        node_neighbour_link_resource_max[u_node] = max(node_neighbour_link_resource_max[u_node], bandwidth)
        node_neighbour_link_resource_max[v_node] = max(node_neighbour_link_resource_max[v_node], bandwidth)

    input = torch.stack(
        [
            node_resource,
            node_neighbour_link_resource_sum,
            # node_neighbour_link_resource_min,
            node_neighbour_link_resource_max
        ],
        dim=1
    )

    return input


# 给定一个网络输入数据input，输出多个乱序的inputs
def get_shuffled_indexes_and_inputs(input, batch_size=10):
    node_num = input.size()[0]
    node_indexes = []

    for i in range(batch_size):
        shuffled_index = torch.randperm(node_num)
        node_indexes.append(shuffled_index)

    node_indexes = torch.stack(node_indexes, dim=0).long()
    inputs = input[node_indexes]
    node_indexes = node_indexes.unsqueeze(dim=2)

    return node_indexes, inputs

def select_node(v_request_cpu, v_request_accuracy, nodes, area):
    # 根据计算能力、数据量大小、数据分布选择合适的节点
    for node in nodes:
        if not node.selected and node.cpu_capacity >= v_request_cpu and node.data_distribution >= v_request_accuracy:
            node.selected = True
            area.selected_devices += 1  # 厂区设备选择数+1
            return node
    return None  # 无可用节点


def allocate_devices_to_area(area, request, nodes):
    if area.allocate_bandwidth(request.bandwidth_required, request.device_num):
        selected_nodes = []
        for _ in range(request.device_num):
            node = select_node(request.cpu_required, request.accuracy_required, nodes, area)
            if node:
                selected_nodes.append(node)
            else:
                return None  # 无法满足设备需求
        return selected_nodes
    return None  # 带宽不足
