#from hpbandster.core.base_iteration import BaseIteration
import numpy as np
import torch

import math

from collections import defaultdict

from naslib.optimizers.core.metaclasses import MetaOptimizer
from naslib.search_spaces.core.query_metrics import Metric


class SuccessiveHalving(MetaOptimizer):
    """
    Optimizer is randomly sampling architectures and queries/trains on the corresponding fidelities.
    After that, models will be discarded according to eta.
    TODO: Implement training
    """

    # training the models is not implemented
    using_step_function = False

    def __init__(
        self,
        config,
        weight_optimizer=torch.optim.SGD,
        loss_criteria=torch.nn.CrossEntropyLoss(),
        grad_clip=None,
    ):
        """
        Initialize a Successive Halving optimizer.

        Args:
            config
            weight_optimizer (torch.optim.Optimizer): The optimizer to
                train the (convolutional) weights.
            loss_criteria (TODO): The loss
            grad_clip (float): Where to clip the gradients (default None).
        """
        super(SuccessiveHalving, self).__init__()
        self.weight_optimizer = weight_optimizer
        self.loss = loss_criteria
        self.grad_clip = grad_clip
        self.budget_max = config.search.budget_max
        self.performance_metric = Metric.VAL_ACCURACY
        self.dataset = config.dataset
        self.end = False
        self.fidelity = config.search.min_fidelity
        self.number_archs = config.search.number_archs
        self.eta = config.search.eta
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.budget_type = config.search.budget_type #is not for one query is overall
        self.fidelity_counter = 0
        self.sampled_archs = []
        self.history = torch.nn.ModuleList()
        self.end = False

        self.optimizer_stats = defaultdict(lambda: defaultdict(list))

    def adapt_search_space(self, search_space, scope=None, dataset_api=None):
        assert (
            search_space.QUERYABLE
        ), "Successsive Halving is currently only implemented for benchmarks."
        self.search_space = search_space.clone()
        self.scope = scope if scope else search_space.OPTIMIZER_SCOPE
        self.dataset_api = dataset_api

    def new_epoch(self):
        """
        Sample a new architecture to train.
        # TODO: with this kind of architekeur, in evaluation only the last fideltiy
        """

        model = torch.nn.Module()  # hacky way to get arch and accuracy checkpointable
        model.arch = self.search_space.clone()
        if len(self.sampled_archs) < self.number_archs:
            model.arch.sample_random_architecture(dataset_api=self.dataset_api)
        else:
            model = self.sampled_archs[self.fidelity_counter]
       
# 		    return(ranks < self.num_configs[self.stage])

        # DONE: define fidelity in multi-fidelity setting
        model.accuracy = model.arch.query(
            self.performance_metric,
            self.dataset,
            epoch=int(self.fidelity), # DONE: adapt this
            dataset_api=self.dataset_api,
        )

        budget = 1 # <-- divide by max_fidelity to get consistent results
        # DONE: make query type secure
        if self.budget_type == 'time':
            # DONE: make dependent on performance_metric
            model.time = model.arch.query( # TODO: this is the time for training from screatch.
                self.performance_metric,
                self.dataset,
                epoch=int(self.fidelity), # DONE: adapt this
                dataset_api=self.dataset_api,
            )
            budget = model.time
        elif not(self.budget_type == "epoch"):
            raise NameError("budget time should be time or epoch")
        # TODO: make this more beautiful/more efficient
        # DONE: we may need to track of all ever sampled archs
        if len(self.sampled_archs) < self.number_archs:
            self.sampled_archs.append(model)
        else:
            self.sampled_archs[self.fidelity_counter] = model
        self.update_optimizer_stats()
        self.fidelity_counter += 1
        # DONE: fidelity is changed for new epoch, what make the wrong values in the dictonary
        self._update_history(model)
        if self.fidelity_counter == self.number_archs:
            self.fidelity = math.floor(self.eta*self.fidelity) #
            self.sampled_archs.sort(key = lambda model: model.accuracy, reverse= True)
            if self.fidelity > self.budget_max:
                    self.end = True
            elif(math.floor(self.number_archs/self.eta)) != 0:
                self.sampled_archs = self.sampled_archs[0:math.floor(self.number_archs/self.eta)] #DONE round
               
            else:
                self.end = True
                self.sampled_archs = [self.sampled_archs[0]]  #but maybe there maybe a different way
            self.number_archs = len(self.sampled_archs)
            self.fidelity_counter = 0
        # TODO: budget equals 
        return budget
        # required if we want to train the models and not only query.
        # architecture_i.parse()
        # architecture_i.train()
        # architecture_i = architecture_i.to(self.device)
        # self.sampled_archs.append(architecture_i)
        # self.weight_optimizers.append(self.weight_optimizer(architecture_i.parameters(), 0.01))

    def _update_history(self, child):
        if len(self.history) < 100:
            self.history.append(child)
        else:
            for i, p in enumerate(self.history):
                if child.accuracy > p.accuracy:
                    self.history[i] = child
                    break

    def get_final_architecture(self):
        """
        Returns the sampled architecture with the lowest validation error.
        """
        return max(self.sampled_archs, key=lambda x: x.accuracy).arch

    def train_statistics(self, report_incumbent=True):

        if report_incumbent:
            best_arch = self.get_final_architecture()
        else:
            best_arch = self.sampled_archs[-1].arch

        return (
            best_arch.query(
                Metric.TRAIN_ACCURACY, self.dataset, dataset_api=self.dataset_api
            ),
            best_arch.query(
                Metric.VAL_ACCURACY, self.dataset, dataset_api=self.dataset_api
            ),
            best_arch.query(
                Metric.TEST_ACCURACY, self.dataset, dataset_api=self.dataset_api
            ),
            best_arch.query(
                Metric.TRAIN_TIME, self.dataset, dataset_api=self.dataset_api
            ),
        )

    def update_optimizer_stats(self):
        """
        Updates statistics of optimizer to be able to create useful plots
        """
        arch = self.sampled_archs[self.fidelity_counter].arch
        arch_hash = hash(self.sampled_archs[self.fidelity_counter])
        # this dict contains metrics to save
        metrics = {
            "train_acc": Metric.TRAIN_ACCURACY,
            "val_acc": Metric.VAL_ACCURACY,
            "test_acc": Metric.TEST_ACCURACY,
            "train_time": Metric.TRAIN_TIME
        }
        for metric_name, metric in metrics.items():
            metric_value = arch.query(
                metric, 
                self.dataset, 
                dataset_api=self.dataset_api, 
                epoch=self.fidelity
            )
            self.optimizer_stats[arch_hash][metric_name].append(metric_value)
        self.optimizer_stats[arch_hash]['fidelity'].append(self.fidelity) 

    def test_statistics(self):
        best_arch = self.get_final_architecture()
        return best_arch.query(Metric.RAW, self.dataset, dataset_api=self.dataset_api)

    def get_op_optimizer(self):
        return self.weight_optimizer
    
    # TODO: we really do not need this
    def get_end(self):
        return self.end

    def get_checkpointables(self):
        return {"model": self.history}