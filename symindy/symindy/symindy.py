import operator
import random
import matplotlib.pyplot as plt
import numpy as np
import pysindy as ps
from deap import base, creator, gp, tools
from sklearn.metrics import r2_score
import networkx as nx
from symindy.symindy.library import library
import multiprocessing
from sklearn.metrics import *

class SymINDy:
    def __init__(
        self,
        ngen=5,
        ntrees=5,
        mutpb=0.8,
        cxpb=0.7,
        dims=2,
        library_name="generalized",
        is_time_dependent=False,
        seed=0,
        score_metrics=r2_score,
        score_metrics_kwargs=None,
        nc=0,
        n_individuals=300,
        sindy_kwargs=None,
        verbose=False,
        sparsity = "n_nodes"
    ):
        """
        Inputs:
                score_metrics - the metrics to use in pySINDy model.score. Default - None, uses
                        R2 coefficient of determination (see sindy model score https://pysindy.readthedocs.io/en/latest/api/pysindy.html
                        and sklearn model evaluation for reference, https://scikit-learn.org/stable/modules/model_evaluation).
                score_metrics_kwargs - key value arguments for scoring function. If None, uses default pySINDy score kwargs.
                is_time_dependent - bool flag, add time as an independent variable, if necessary. Default False

        """
        self.ngen = ngen
        self.ntrees = ntrees
        self.mutpb = mutpb
        self.cxpb = cxpb
        self.dims = dims
        self.library_name = library_name
        self.seed = seed
        random.seed(seed)
        self.is_time_dependent = is_time_dependent
        self.score_metrics = score_metrics
        self.score_metrics_kwargs = score_metrics_kwargs
        self.nc = nc
        self.n_individuals = n_individuals
        self.sindy_kwargs = sindy_kwargs
        self.sparsity = sparsity
        self.verbose = verbose


    def configure_DEAP(self, ntrees=5, nc=0, dimensions=2, max_depth=2, is_time_dependent=False):
        """
        Inputs:
                ntrees -int, number of trees defining an individual. Defualt=5.
                nc -int, number of nonlinear parameters (symbolic constants 
                    associated to the individual). Defualt=0.
                dimensions - int, read from txt files as n columns
                is_time_dependent - flag, is the system is time-dependent
        """

        def _random_mating_operator(ind1, ind2):
            roll = random.random()
            if roll < 0.5:
                return gp.cxOnePoint(ind1, ind2)
            elif roll < 1.5:
                return gp.cxOnePointLeafBiased(ind1, ind2, termpb=0.5)

        def _random_mutation_operator(individual):
            roll = random.random()
            if roll < 0.5:
                return gp.mutInsert(individual, pset=pset)
            elif roll < 0.66:
                return gp.mutShrink(individual)
            elif roll < 2.66:
                return gp.mutNodeReplacement(individual, pset=pset)

        def _rename_args(pset, nc, dimensions, is_time_dependent):
            '''Rename arguments in primitive set.
            Inputs:
                pset - primitive set
                nc -int, number of nonlinear parameters (symbolic constants 
                    associated to the individual).
                dimensions - int, read from txt files as n columns
                is_time_dependent - flag, is the system is time-dependent
            Outputs:
                pset - primitive set with renamed arguments.
                    (first nc arguments come from nc, from nc to
                    dimensions + nc - dimensions, last argument is time if
                    is_time_dependent is True.)
            '''
            argnames = {}
            for dim in range(dimensions):
               argnames["ARG{}".format(dim)]="x{}".format(dim)
            for i in range(nc):
                argnames["ARG{}".format(i+dimensions)] = "x{}".format(i)
            if is_time_dependent:
                argnames["ARG{}".format(len(argnames))]='t'
            pset.renameArguments(**argnames)
            return pset

        def _create_toolbox(pset, ntrees, max_depth=2):
            '''Create a deap toolbox, creator and history objects.
            Inputs:
                pset - primitive set to register in the toolbox
                ntrees - number of trees of symbolic expressions per subindividual
            Outputs:
                toolbox, creator, history
            '''
            from deap import creator #TODO figure out why the globally imported creator is not seen inside this function.
            creator.create("FitnessMax", base.Fitness, weights=(1.0,)) 
            # subindividual is a primitive tree which is populated from pset
            creator.create("Subindividual", gp.PrimitiveTree)  
            creator.create("Individual", list, fitness=creator.FitnessMax)
            toolbox = base.Toolbox()
            toolbox.register("expr", gp.genHalfAndHalf, pset=pset, type_=pset.ret, min_=0, max_=max_depth)
            toolbox.register("subindividual", tools.initIterate, creator.Subindividual, toolbox.expr)
            toolbox.register("individual",tools.initRepeat,creator.Individual,\
                toolbox.subindividual, n=ntrees)
            toolbox.register("population", tools.initRepeat, list, toolbox.individual)
            toolbox.register("compile", gp.compile, pset=pset)
            toolbox.register("select", tools.selTournament, tournsize=2)
            toolbox.register("mate", _random_mating_operator)
            toolbox.register("mutate", _random_mutation_operator)
            history = tools.History()
            toolbox.decorate("mate", history.decorator)
            toolbox.decorate("mutate", history.decorator)
            toolbox.decorate("mate", gp.staticLimit(key=operator.attrgetter("height"), max_value=2))
            toolbox.decorate("mutate", gp.staticLimit(key=operator.attrgetter("height"), max_value=2))
            return toolbox, creator, history

        self.max_depth = max_depth
        lib = library(
            nc,
            dimensions,
            is_time_dependent=self.is_time_dependent,
            library_name=self.library_name
        )
        pset = lib()
        
        pset = _rename_args(pset, nc, dimensions, self.is_time_dependent)
        
        toolbox, creator, history = _create_toolbox(pset, ntrees, max_depth=self.max_depth)
        return toolbox, creator, pset, history


    @staticmethod
    def evalSymbReg(
        individual,
        ntrees,
        max_depth,
        toolbox,
        x_train,
        x_dot_train,
        time_rec_obs=None,
        sindy_kwargs=None,
        score_metrics=None,
        score_metrics_kwargs=None,
        flag_solution=False,
        tr_te_ratio=None,
        sparsity="n_zero_nodes"
    ):
        """Fitness function to evaluate symbolic regression.
        For additional documentation see SINDy model docs
        https://pysindy.readthedocs.io/en/latest/api/pysindy.html#module-pysindy.pysindy
        Inputs:
                individual - list of subindividuals (with invalid fitness)
                ntrees - number of trees of symbolic expressions per subindividual
                toolbox - deap base toolbox instance
                x_train - np array, training data
                x_dot_train - precomputed derivatives of the training data, optional. Defualt=None, no
                        precomputed derivatives (SINDY computes it using specified differentiation method).
                time_rec_obs - (float, numpy array of shape (n_samples,), or list of numpy arrays, optional (default None)) –
                        If t is a float, it specifies the timestep between each sample.
                        If array-like, it specifies the time at which each sample was collected.
                sindy_kwargs - dictionary with kwargs for SINDY. Default=None, no kwargs
                tr_te_ratio -  float, ratio of train vs test split of the training data when fitting pysindy model.
                    If None, no train test split. If not none, no train test split is doen. Default = 0.8
                sparsity - str, sparsity penalty for the optimization problem. Default - n_zero_nodes
        Outputs:
                [fitness] - list with fitness value. NB - DEAP requires output to be iterable (so, it shall be
                        a tuple or a list).
        """
        def validate_input(x_train):
            '''check input e.g. x_train shall have at least 3 timepoints (requirement of pysindy
            differentiation package).'''
            if x_train.shape[0] < 3:
                raise ValueError("x_train shall have at least 3 timepounts!")

        def create_sindy_model(individual, toolbox, sindy_kwargs):
            '''Create sindy model instance with the custom library generated by DEAP'''
            # Transform the tree expression in a callable function
            sr_functions = []
            for i in range(ntrees):
                sr_functions.append(toolbox.compile(expr=individual[i]))
            library = ps.CustomLibrary(library_functions=sr_functions)
            model = ps.SINDy(feature_library=library, **sindy_kwargs)
            return model

        def fit_sindy_model(model, x_train, x_dot_train=None, time_rec_obs=None, fitkwargs={"quiet": True}):
            '''Fit pysindy model.
            '''
            model.fit(x_train, t=time_rec_obs, x_dot=x_dot_train, **fitkwargs)
            return model

        def score_sindy_model(model, x_train, time_rec_obs, x_dot_train,
            score_metrics, score_metrics_kwargs):
            '''Get the score of the sindy model fitted with
            symbolic expressions generated by DEAP'''
            # TODO maybe add corss-validation fitting
            #! Uses corr coef of thresholded least square
            fitness = model.score(
                x_train,
                t=time_rec_obs,
                x_dot=x_dot_train,
                u=None,
                multiple_trajectories=False,
                metric=score_metrics,
                **score_metrics_kwargs)
            return model, fitness

        if sindy_kwargs is None:
            sindy_kwargs = {}
        if score_metrics is None:
            score_metrics = r2_score
        if score_metrics_kwargs is None:
            score_metrics_kwargs = {}
        
        validate_input(x_train)

        model = create_sindy_model(individual, toolbox, sindy_kwargs)

        # if train test split, fit the model on train set and score on test set
        split = lambda x, ratio: (x[:int(ratio*len(x))], x[int(ratio*len(x)):]) if x is not None else (None, None)
        
        if tr_te_ratio is not None:
            x_train_tr, x_train_te = split(x_train, tr_te_ratio)
            x_dot_train_tr, x_dot_train_te = split(x_dot_train, tr_te_ratio)
            time_tr, time_te = split(time_rec_obs, tr_te_ratio)
            model =fit_sindy_model(model, x_train_tr, x_dot_train_tr, time_tr)
            model, fitness = score_sindy_model(model, x_train_te, time_te, x_dot_train_te,
                score_metrics, score_metrics_kwargs)
        else:
            model = fit_sindy_model(model, x_train, x_dot_train, time_rec_obs)
            model, fitness = score_sindy_model(model, x_train, time_rec_obs, x_dot_train,
                score_metrics, score_metrics_kwargs)

        # Sparsity penalty - coerce the model to keep nnodes as small as possible
        if sparsity=="n_zero_nodes":
            #n_samples, nterms = model.coefficients().shape # terms - subindivuduals and their interaction: len(individual)*n_samples
            ind_coefs_list = np.split(model.coefficients().T.reshape(-1), ntrees)
            n_nodes=0
            for i in range(ntrees):
                # if zero subindividual
                if np.all(ind_coefs_list[i]==0): 
                    continue
                n_nodes += len(individual[i])
            # len(individual)* # 2 max n inputs among dict funcs, (1+2) - max depath
            max_nnodes = 2**(1+max_depth)*ntrees 
            # normalize n_nodes by max n_nodes (self.max_depth)
            fitness -= n_nodes/max_nnodes
        if not flag_solution:
            return [fitness, ]
        else:
            return model


    # static method shall solve problems with functool.partial in toolbox.register
    @staticmethod
    def my_eaSimple(
        population,
        toolbox_local,
        cxpb,
        mutpb,
        ngen,
        ntrees,
        stats=None,
        halloffame=None,
        verbose=False,
    ):
        """
        Takes in a population and evolves it in place using the varAnd() method.
        Returns the optimized population and a Logbook with the statistics of the evolution.

        Inputs:
                population – A list of individuals
                toolbox – A DEP Toolbox class instance, that contains the evolution operators.
                cxpb – The probability of mating two individuals.
                mutpb – The probability of mutating an individual.
                ngen – The number of generation.
                ntrees - number of trees of symbolic expressions per subindividual
                stats – A DEAP Statistics object that is updated inplace. Default=None.
                halloffame – A DEAP HallOfFame object that will contain the best individuals. Default=None.
                verbose – Whether or not to log the statistics. Default=__debug__.
        Outputs:
                population: The final population
                logbook - a logbook object with the statistics of the evolution.

        Pseudo code of eaSimple from DEAP
        evaluate(population)
                for g in range(ngen):
                        population = select(population, len(population))
                        offspring = varAnd(population, toolbox, cxpb, mutpb)
                        evaluate(offspring)
                        population = offspring
        """

        def _my_varAnd(population, toolbox_local, cxpb, mutpb):
            """
            Part of an evolutionary algorithm applying only the variation part (crossover and mutation).
            See https://deap.readthedocs.io/en/master/api/algo.html#deap.algorithms.varAnd for the reference.
            Inputs:
                    population - a list of individuals to vary. It is recommended that the population is created
                            with the toolbox.register method of toolbox object instance from DEAP
                    toolbox_local
                    cxpb - float, is the probability with which two individuals are crossed
                    mutpb - float, is the probability for mutating an individual
            Outputs:
                    offspring - a "list" of varied individuals that are independent of their parents (deepcopied)
                    halloffame - deap halloffame object. Contains the best individual that
                        ever lived in the popultion (best over all generations)
            """
            # Create an offspring list sampled from the population
            offspring = [toolbox_local.clone(ind) for ind in population]

            # Apply crossover and mutation on the offspring
            for i in range(1, len(offspring), 2):
                # for h_component in range(ntrees):
                if random.random() < cxpb:
                    h_component = random.randint(
                        0, ntrees - 1
                    )  # where do we define ntrees?
                    (
                        offspring[i - 1][h_component],
                        offspring[i][h_component],
                    ) = toolbox_local.mate(
                        offspring[i - 1][h_component], offspring[i][h_component]
                    )
                    del offspring[i - 1].fitness.values, offspring[i].fitness.values

            for i in range(len(offspring)):
                for h_component in range(ntrees):
                    if random.random() < mutpb:
                        # h_component = random.randint(0, ntrees-1)
                        (offspring[i][h_component],) = toolbox_local.mutate(
                            offspring[i][h_component]
                        )
                        del offspring[i].fitness.values
            return offspring

        logbook = tools.Logbook()
        logbook.header = ["gen", "nevals"] + (stats.fields if stats else [])

        # Evaluate the fitness of the first population
        invalid_ind = [ind for ind in population if not ind.fitness.valid]
        fitnesses = toolbox_local.map(toolbox_local.evaluate, invalid_ind)
        for ind, fit in zip(invalid_ind, fitnesses):
            ind.fitness.values = fit
            if verbose:
                print('Fitness: '+str(fit))

        if halloffame is not None:
            halloffame.update(population)
        record = stats.compile(population) if stats else {}
        logbook.record(gen=0, nevals=len(invalid_ind), **record)
        if verbose:
            logbook.stream

        # Begin the generational process
        for gen in range(1, ngen + 1):
            # Select the next generation individuals
            offspring = toolbox_local.select(population, len(population))

            # Vary the pool of individuals
            offspring = _my_varAnd(offspring, toolbox_local, cxpb, mutpb)

            # Evaluate the individuals with an invalid fitness
            invalid_ind = [ind for ind in offspring if not ind.fitness.valid]
            fitnesses = toolbox_local.map(toolbox_local.evaluate, invalid_ind)
            for ind, fit in zip(invalid_ind, fitnesses):
                ind.fitness.values = fit

            # Update the hall of fame with the generated individuals
            if halloffame is not None:
                halloffame.update(offspring)

            # Replace the current population by the offspring
            population[:] = offspring

            # Append the current generation statistics to the logbook
            record = stats.compile(population) if stats else {}
            logbook.record(gen=gen, nevals=len(invalid_ind), **record)
            if verbose:
                print(logbook.stream)
                for i in range(ntrees):
                    print(halloffame[0][i])
        return population, logbook, halloffame

    @staticmethod
    def init_stats():
        stats_fit = tools.Statistics(lambda ind: ind.fitness.values)
        stats_size = tools.Statistics(len)
        mstats = tools.MultiStatistics(fitness=stats_fit, size=stats_size)
        mstats.register("avg", np.mean)
        mstats.register("std", np.std)
        mstats.register("min", np.min)
        mstats.register("max", np.max)
        return mstats

    def fit(self, x_train, x_dot_train=None, time_rec_obs=None):
        """Train SymINDy model on the train data."""

        # Initiate DEAP
        toolbox, creator, pset, history = self.configure_DEAP(
            ntrees=self.ntrees, nc=self.nc, dimensions=self.dims
        )

        # Register evaluation fucntion (add arguments from init)
        toolbox.register(
            "evaluate",
            self.evalSymbReg,
            ntrees=self.ntrees,
            max_depth = self.max_depth,
            toolbox=toolbox,
            x_train=x_train,
            x_dot_train=x_dot_train,
            time_rec_obs=time_rec_obs,
            sindy_kwargs=self.sindy_kwargs,
            score_metrics=self.score_metrics,
            score_metrics_kwargs=self.score_metrics_kwargs,
            flag_solution=False,
            tr_te_ratio=0.8,
            sparsity=self.sparsity
        )

        # Register function to train SINDy model and retrieve it
        toolbox.register(
            "retrieve_model",
            self.evalSymbReg,
            ntrees=self.ntrees,
            max_depth = self.max_depth,
            toolbox=toolbox,
            x_train=x_train,
            x_dot_train=x_dot_train,
            time_rec_obs=time_rec_obs,
            sindy_kwargs=self.sindy_kwargs,
            score_metrics=self.score_metrics,
            score_metrics_kwargs=self.score_metrics_kwargs,
            flag_solution=True,
            tr_te_ratio=0.8,
            sparsity=self.sparsity
        )

        mstats = self.init_stats()
        # number of individuals in a population
        pop = toolbox.population(n=self.n_individuals)
        hof_ = tools.HallOfFame(1)

        # Run the evolution
        pop, log, hof = self.my_eaSimple(
            pop,
            toolbox,
            cxpb=self.cxpb,
            mutpb=self.mutpb,
            ngen=self.ngen,
            ntrees=self.ntrees,
            stats=mstats,
            halloffame=hof_,
            verbose=self.verbose,
        )

        # Train SINDy model with the best individual
        final_model = toolbox.retrieve_model(hof[0])

        # store the data as attributes
        self.x_train = x_train
        self.x_dot_train = x_dot_train
        self.time_rec_obs = time_rec_obs

        self.toolbox = toolbox
        self.creator = creator
        self.pset = pset
        self.history = history
        self.population = pop
        self.log = log
        self.hof = hof # best individual that ever lived
        self.final_model = final_model
        
        # print estimated model
        print('\n')
        for i in range(self.ntrees):
            print('Estimated library functions: f{0}: {1}'.format(str(i), hof[0][i]))
        print('Estimated dynamical system: \n')
        self.final_model.print()
        print('\n')

    def predict(self, x0, time, simulate_kwargs=None, predict_kwargs=None):
        '''
        Predict x and xdot using fitted (trained) model.
        Note, that if you use the model with train-test sets, x0 shall be the first time
        observation of the test set.
        Inputs:
            x0 - initial condition for the prediction of xtest. 
            time - int or array, time corresponding to predictions
            simulate_kwargs - dict of args for pysindy.simulate. Default=None, no kwargs
            predict_kwargs - dict of args for pysindy.predict. Default=None, no kwargs
        Outputs:
            x_te_pred - predicted x (for the rime interval time)
            xdot_te_pred - xdot predicted from x_te_pred
        '''
        if simulate_kwargs is None:
            simulate_kwargs = {}
        if predict_kwargs is None:
            predict_kwargs = {}
        # simulate xtest from the initial condition
        x_te_pred = self.final_model.simulate(x0, time, **simulate_kwargs)
        # predict xdot from x_te_pred
        xdot_te_pred = self.final_model.predict(x_te_pred)
        return x_te_pred, xdot_te_pred
    
    @staticmethod
    def score(x, x_pred, xdot, xdot_pred, metric=None, metric_kwargs=None):
        '''
        Computes the specified score between (x, x_pred) and (xdot and xdot_pred).
        Inputs:
            x
            x_pred
            xdot 
            xdot_pred
            metric - metric to use
            kwargs - kwrargs for the metric
        Outputs:
            x_score - score for x prediction
            xdot_score - score for xdot prediction (None if xdot is None)
        '''
        if metric is None:
            metric = r2_score
        if metric_kwargs is None:
            metric_kwargs = {}
        x_score = metric(x, x_pred, **metric_kwargs)
        try:
            xdot_score = metric(xdot, xdot_pred, **metric_kwargs)
        except ValueError:
            xdot_score = None
        return x_score, xdot_score

    def plot_trees(self, show=False):
        '''Plot tree of the best individuals (hof[0]).'''
        expr=self.hof[0]
        fig, axs = plt.subplots(int(np.floor(self.ntrees/2)), 
            int(np.ceil(self.ntrees/2)), figsize=(16,9))
        for i, ax in zip(range(self.ntrees), np.ravel(axs)):
            nodes, edges, labels = gp.graph(expr[i])
            g = nx.Graph()
            g.add_nodes_from(nodes)
            g.add_edges_from(edges)
            pos=nx.nx_agraph.pygraphviz_layout(g, prog="dot")
            nx.draw(g, pos, with_labels=True, ax=ax, labels=labels,
            node_color='#99CCFF', edge_color="k", font_size=20, font_color="k")
            ax.set_axis_off()
        plt.margins(0.2)
        plt.axis("off")
        plt.tight_layout()
        if show == True:
            plt.show()
        return fig, ax

if __name__ == "__main__":
    pass