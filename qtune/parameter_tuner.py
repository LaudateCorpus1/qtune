from typing import Sequence, List, Tuple

import numpy as np
import pandas as pd

from qtune.evaluator import Evaluator
from qtune.solver import Solver
from qtune.storage import HDF5Serializable


class ParameterTuner(metaclass=HDF5Serializable):
    """This class tunes a specific set of parameters which are defined by the given evaluators."""

    def __init__(self,
                 evaluators: Sequence[Evaluator],
                 solver: Solver,
                 tuned_voltages=None,
                 last_voltage=None,
                 last_parameter_values=None,
                 last_parameter_covariances=None):
        self._tuned_voltages = tuned_voltages or []

        self._last_voltage = last_voltage

        if last_parameter_values is None:
            self._last_parameter_values = pd.Series(index=solver.target.index)
        else:
            self._last_parameter_values = last_parameter_values

        if last_parameter_covariances is None:
            self._last_parameter_covariances = pd.Series(index=solver.target.index)
        else:
            self._last_parameter_covariances = last_parameter_covariances

        self._evaluators = tuple(evaluators)

        parameters = sorted(parameter
                            for evaluator in self._evaluators
                            for parameter in evaluator.parameters)
        if len(parameters) != len(set(parameters)):
            raise ValueError('Parameter duplicates: ', {p for p in parameters if parameters.count(p) > 1})

        self._solver = solver

        assert set(self.target.index) == set(parameters)

    @property
    def solver(self) -> Solver:
        return self._solver

    @property
    def target(self) -> pd.DataFrame:
        return self.solver.target

    @property
    def parameters(self) -> Sequence[str]:
        """Alphabetically sorted parameters"""
        return self.solver.target.index

    @property
    def last_parameter_covariance(self) -> Tuple[pd.Series, pd.Series]:
        """Last parameter values with covariances."""
        return self._last_parameter_values, self._last_parameter_covariances

    @property
    def tuned_voltages(self) -> List[pd.Series]:
        """A list of the positions where the parameter set was successfully tuned."""
        return self._tuned_voltages

    def evaluate(self) -> Tuple[pd.Series, pd.Series]:
        #  no list comprehension for easier debugging
        parameters = []
        variances = []
        for evaluator in self._evaluators:
            parameter, variance = evaluator.evaluate()
            parameters.append(parameter)
            variances.append(variance)
        return pd.concat(parameters)[self.parameters], pd.concat(variances)[self.parameters]

    def is_tuned(self, voltages: pd.Series) -> bool:
        """
        Checks if current parameters already match the requirements stated in the solver. Thereby these parameters are
        evaluated.
        :param voltages: current voltages
        :return: True if requirement is met. False otherwise.
        """
        raise NotImplementedError()

    def get_next_voltages(self) -> pd.Series:
        """The next voltage in absolute values.
        :return: next_voltages
        """
        raise NotImplementedError()

    def to_hdf5(self) -> dict:
        return dict(evaluators=self._evaluators,
                    solver=self._solver,
                    tuned_voltages=self._tuned_voltages,
                    last_voltage=self._last_voltage,
                    last_parameter_values=self._last_parameter_values,
                    last_parameter_covariances=self._last_parameter_covariances)


class SubsetTuner(ParameterTuner):
    """This tuner uses only a subset of gates to tune the parameters"""

    def __init__(self, evaluators: Sequence[Evaluator], gates: Sequence[str],
                 **kwargs):
        """
        :param evaluators:
        :param gates: Gates which are used to tune the parameters
        :param tuned_voltages:
        :param last_voltage:
        :param last_parameter_values:
        """
        super().__init__(evaluators, **kwargs)

        self._gates = sorted(gates)

    def is_tuned(self, voltages: pd.Series) -> bool:
        current_parameters, current_variances = self.evaluate()

        self._solver.update_after_step(voltages, current_parameters, current_variances)

        self._last_voltage = voltages
        self._last_parameter_values = current_parameters[self._last_parameter_values.index]
        self._last_parameter_covariances = current_variances[self._last_parameter_values.index]
        if ((self.target.desired - current_parameters).abs().fillna(0.) < self.target['tolerance'].fillna(
                np.inf)).all():
            self._tuned_voltages.append(voltages)
            return True
        else:
            return False

    def get_next_voltages(self):
        solver_voltage = self._solver.suggest_next_position()
        result = pd.Series(self._last_voltage)

        result[solver_voltage.index] = solver_voltage

        return result

    def to_hdf5(self):
        parent_dict = super().to_hdf5()
        return dict(parent_dict, gates=self._gates)


class SensingDotTuner(ParameterTuner):
    """
    This tuner directly tunes to voltage points of interest. The evaluators return positions
    """

    def __init__(self, cheap_evaluators: Sequence[Evaluator], expensive_evaluators: Sequence[Evaluator],
                 gates: Sequence[str], **kwargs):
        """

        :param cheap_evaluators: An evaluator with little measurement costs. (i.e. one dimensional sweep of gates
        defining the sensing dot.) This evaluator needs to detect at least if the parameter already meets the
        conditions defined in the target. It can also detect additional information (i.e. voltages with higher contrast
        in the sensing dot.)
        :param expensive_evaluators: An evaluator which finds the optimal position of the sensing dot, or information
        leading to its position. (i.e. two dimensional sensing dot scan.)
        :param gates: The gates which will be used to tune the parameters
        :param min_threshhold: If the parameters are below this threshold, the experiment is not tuned. This doesnt
        regard the optimal signal found but only the current one.
        :param cost_threshhold: If the parameters are below this threshold, the expensive evaluation will be used.
        :param kwargs: Must contain the argument 'solver' for the init function of the ParameterTuner parent class.
        """
        # the parameters can be specified using last_parameter_values and last_parameters_covariances or they will be
        # deducted from the evaluators.
        last_parameter_values_covariances = []
        for string in ["last_parameter_values", "last_parameter_covariances"]:
            if string not in kwargs:
                parameter_names = [name for evaluator_list in [cheap_evaluators, expensive_evaluators]
                                   for evaluator in evaluator_list
                                   for name in evaluator.parameters]
                parameter_names = set(parameter_names)
                parameter_names = sorted(list(parameter_names))
                series = pd.Series(index=parameter_names)
            else:
                series = kwargs[string]
            last_parameter_values_covariances.append(series)

        super().__init__(cheap_evaluators, last_parameter_values=last_parameter_values_covariances[0],
                         last_parameter_covariances=last_parameter_values_covariances[1], **kwargs)
        self._gates = sorted(gates)
        self._cheap_evaluators = cheap_evaluators
        self._expensive_evaluators = expensive_evaluators

    def is_tuned(self, voltages: pd.Series):
        current_parameter, variances = self.evaluate(cheap=True)
        solver_voltages = voltages[self._gates]
        self._last_voltage = voltages
        self._last_parameter_values[current_parameter.index] = current_parameter[current_parameter.index]
        self._last_parameter_covariances[current_parameter.index] = variances[current_parameter.index]

        if current_parameter.le(self.target["minimum"]).any():
            if current_parameter.le(self.target["cost_threshold"]).any():
                current_parameter, variances = self.evaluate(cheap=False)
                self._last_parameter_values[current_parameter.index] = current_parameter[current_parameter.index]
                self._last_parameter_covariances[current_parameter.index] = variances[current_parameter.index]

            self.solver.update_after_step(solver_voltages, current_parameter, variances)
            return False
        else:
            self._tuned_voltages.append(voltages)
            return True

    def get_next_voltages(self):
        solver_step = self._solver.suggest_next_position()

        return self._last_voltage.add(solver_step, fill_value=0)

    def evaluate(self, cheap=True, **kwargs) -> (pd.Series, pd.Series):
        #  no list comprehension for easier debugging
        #  cheap = kwargs["cheap"]
        parameters = []
        variances = []
        if cheap:
            for evaluator in self._cheap_evaluators:
                parameter, variance = evaluator.evaluate()
                parameters.append(parameter)
                variances.append(variance)
        else:
            for evaluator in self._expensive_evaluators:
                parameter, variance = evaluator.evaluate()
                parameters.append(parameter)
                variances.append(variance)
        return pd.concat(parameters).sort_index(), pd.concat(variances).sort_index()

    def to_hdf5(self):
        return dict(cheap_evaluators=self._cheap_evaluators,
                    expensive_evaluators=self._expensive_evaluators,
                    gates=self._gates,
                    solver=self.solver,
                    last_parameter_values=self._last_parameter_values,
                    last_parameter_covariances=self._last_parameter_covariances)
