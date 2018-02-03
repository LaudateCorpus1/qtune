# -*- coding: utf-8 -*-
"""
Created on Thu Nov 23 14:39:28 2017

@author: teske

The chargediagram will be implemented as class.
"""
import numpy as np
import pandas as pd
from typing import Tuple
from qtune.experiment import Measurement
from qtune.Basic_DQD import BasicDQD
from qtune.GradKalman import GradKalmanFilter
from qtune.util import find_lead_transition


class ChargeDiagram:
    charge_line_scan_lead_A = Measurement('line_scan', center=0., range=4e-3,
                                          gate='RFA', N_points=320,
                                          ramptime=.001,
                                          N_average=7,
                                          AWGorDecaDAC='DecaDAC')

    charge_line_scan_lead_B = Measurement('line_scan', center=0., range=4e-3,
                                          gate='RFB', N_points=320,
                                          ramptime=.001,
                                          N_average=7,
                                          AWGorDecaDAC='DecaDAC')

    def __init__(self, dqd: BasicDQD, central_position=np.asarray([-0.15e-3, -0.77e-3]),
                 charge_line_scan_lead_A: Measurement = None,
                 charge_line_scan_lead_B: Measurement = None):
        self.dqd = dqd
        self.central_position = central_position
        self.position_lead_A = 0.
        self.position_lead_B = 0.
        self.grad_kalman = GradKalmanFilter(2, 2, initX=np.zeros((2, 2), dtype=float))

        if charge_line_scan_lead_A is not None:
            self.charge_line_scan_lead_A = charge_line_scan_lead_A

        if charge_line_scan_lead_B is not None:
            self.charge_line_scan_lead_B = charge_line_scan_lead_B

    def measure_positions(self) -> Tuple[float, float]:
        current_gate_voltages = self.dqd.read_gate_voltages()
        RFA_eps = pd.Series(-4e-3, ['RFA'])
        RFB_eps = pd.Series(-4e-3, ['RFB'])
        #voltages_for_pos_a = current_gate_voltages.add(-4*RFB_eps, fill_value=0)
        self.dqd.set_gate_voltages(RFB_eps)
        data_A = self.dqd.measure(self.charge_line_scan_lead_A)
        self.position_lead_A = find_lead_transition(data_A,
                                                    float(self.charge_line_scan_lead_A.parameter["center"]),
                                                    float(self.charge_line_scan_lead_A.parameter["range"]),
                                                    self.charge_line_scan_lead_A.parameter["N_points"])

        #voltages_for_pos_b = current_gate_voltages.add(-4*RFA_eps, fill_value=0)
        self.dqd.set_gate_voltages(RFA_eps)
        data_B = self.dqd.measure(self.charge_line_scan_lead_B)
        self.position_lead_B = find_lead_transition(data_B,
                                                    float(self.charge_line_scan_lead_B.parameter["center"]),
                                                    float(self.charge_line_scan_lead_B.parameter["range"]),
                                                    self.charge_line_scan_lead_B.parameter["N_points"])
        self.dqd.set_gate_voltages(current_gate_voltages)
        return self.position_lead_A, self.position_lead_B

    def calculate_gradient(self):
        current_gate_voltages = self.dqd.read_gate_voltages()

        BA_eps = pd.Series(3e-3, ['BA'])
        BB_eps = pd.Series(3e-3, ['BB'])

        BA_inc = current_gate_voltages.add(BA_eps, fill_value=0)
        BA_dec = current_gate_voltages.add(-BA_eps, fill_value=0)

        BB_inc = current_gate_voltages.add(BB_eps, fill_value=0)
        BB_dec = current_gate_voltages.add(-BB_eps, fill_value=0)

        self.dqd.set_gate_voltages(BA_inc)
        pos_A_BA_inc, pos_B_BA_inc = self.measure_positions()

        self.dqd.set_gate_voltages(BA_dec)
        pos_A_BA_dec, pos_B_BA_dec = self.measure_positions()

        self.dqd.set_gate_voltages(BB_inc)
        pos_A_BB_inc, pos_B_BB_inc = self.measure_positions()

        self.dqd.set_gate_voltages(BB_dec)
        pos_A_BB_dec, pos_B_BB_dec = self.measure_positions()

        gradient = np.zeros((2, 2), dtype=float)
        gradient[0, 0] = (pos_A_BA_inc - pos_A_BA_dec) / 3e-3
        gradient[0, 1] = (pos_A_BB_inc - pos_A_BB_dec) / 3e-3
        gradient[1, 0] = (pos_B_BA_inc - pos_B_BA_dec) / 3e-3
        gradient[1, 1] = (pos_B_BB_inc - pos_B_BB_dec) / 3e-3

        self.dqd.set_gate_voltages(current_gate_voltages)

        return gradient.copy()

    def initialize_kalman(self, initX=None, initP=None, initR=None, alpha=1.02):
        if initX is None:
            initX = self.calculate_gradient()
        self.grad_kalman = GradKalmanFilter(2, 2, initX=initX, initP=initP, initR=initR, alpha=alpha)

    def center_diagram(self, remeasure_positions: bool=True):
        if remeasure_positions:
            positions = np.asarray((self.measure_positions()))
        else:
            positions = np.asarray([self.position_lead_A, self.position_lead_B])
        while np.linalg.norm(positions - self.central_position) > 0.2e-3:
            current_position = np.asarray((self.position_lead_A, self.position_lead_B))
            du = np.linalg.solve(self.grad_kalman.grad, current_position - self.central_position)
            if np.linalg.norm(du) > 3e-3:
                du = du*3e-3/np.linalg.norm(du)

            diff = pd.Series(-1*du, ['BA', 'BB'])
            new_gate_voltages = self.dqd.read_gate_voltages().add(diff, fill_value=0)
            self.dqd.set_gate_voltages(new_gate_voltages)

            positions = list(self.measure_positions())
            dpos = (positions[0] - current_position[0], positions[1] - current_position[1])
            self.grad_kalman.update(-1*du, dpos, hack=False)


class PredictionChargeDiagram(ChargeDiagram):
    def __init__(self, dqd: BasicDQD, tunable_gates: pd.Series, central_position=np.asarray([-0.15e-3, -0.77e-3]),
                 charge_line_scan_lead_A: Measurement = None,
                 charge_line_scan_lead_B: Measurement = None):
        super().__init__(dqd=dqd, central_position=central_position, charge_line_scan_lead_A=charge_line_scan_lead_A,
                         charge_line_scan_lead_B=charge_line_scan_lead_B)
        self.tunable_gates = tunable_gates
        self.grad_kalman_prediction = GradKalmanFilter(nGates=4, nParams=3, initX=np.zeros((3, 4), dtype=float))

    def calculate_prediction_gradient(self, n_repetitions: int=5, delta_u: float=2e-3):
        if self.tunable_gates is None:
            print("Please specify the tunable gates!")
            return

        positive_detune_pd = pd.Series()
        negative_detune_pd = pd.Series()
        gradient_pd = pd.DataFrame(index=["pos_a", "pos_b", "qpc"], columns=self.tunable_gates.index)
        gradient_std_pd = pd.DataFrame(index=["pos_a", "pos_b", "qpc"], columns=self.tunable_gates.index)
        measurement_std_pd = pd.Series(index=["pos_a", "pos_b", "qpc"])
        current_gate_voltages = self.dqd.read_gate_voltages()[self.tunable_gates.index]
        for gate in self.tunable_gates.index:
            d_voltage = pd.Series(data=[delta_u], index=[gate])
            new_gate_voltages = current_gate_voltages.add(d_voltage, fill_value=0.)
            self.dqd.set_gate_voltages(new_gate_voltages.copy())
            positive_detune_pd[gate] = np.zeros((n_repetitions, 3))
            for i in range(n_repetitions):
                tuning_output, qpc_voltage = self.dqd.tune_qpc()
                positive_detune_pd[gate][i, 2] = qpc_voltage["qpc"][0]
                self.measure_positions()
                positive_detune_pd[gate][i, 0] = self.position_lead_A
                positive_detune_pd[gate][i, 1] = self.position_lead_B
            new_gate_voltages = current_gate_voltages.add(-1.*d_voltage, fill_value=0.)
            self.dqd.set_gate_voltages(new_gate_voltages.copy())
            negative_detune_pd[gate] = np.zeros((n_repetitions, 3))
            for i in range(n_repetitions):
                tuning_output, qpc_voltage = self.dqd.tune_qpc()
                negative_detune_pd[gate][i, 2] = qpc_voltage["qpc"][0]
                self.measure_positions()
                negative_detune_pd[gate][i, 0] = self.position_lead_A
                negative_detune_pd[gate][i, 1] = self.position_lead_B
            gradient_pd[gate]["pos_a"] = (
                                         positive_detune_pd[gate][:, 0] - negative_detune_pd[gate][:, 0]) / 2. / delta_u
            gradient_pd[gate]["pos_b"] = (
                                         positive_detune_pd[gate][:, 1] - negative_detune_pd[gate][:, 1]) / 2. / delta_u
            gradient_pd[gate]["qpc"] = (positive_detune_pd[gate][:, 2] - negative_detune_pd[gate][:, 2]) / 2. / delta_u
            gradient_std_pd[gate]["pos_a"] = np.nanstd(gradient_pd[gate]["pos_a"])
            gradient_std_pd[gate]["pos_b"] = np.nanstd(gradient_pd[gate]["pos_b"])
            gradient_std_pd[gate]["qpc"] = np.nanstd(gradient_pd[gate]["qpc"])
        self.dqd.set_gate_voltages(current_gate_voltages.copy())
        measurement_std_pd["pos_a"] = np.nanstd(gradient_pd["N"]["pos_a"])
        measurement_std_pd["pos_b"] = np.nanstd(gradient_pd["N"]["pos_b"])
        measurement_std_pd["qpc"] = np.nanstd(gradient_pd["N"]["qpc"])
        for gate in self.tunable_gates.index:
            gradient_pd[gate]["pos_a"] = np.nanmean(gradient_pd[gate]["pos_a"])
            gradient_pd[gate]["pos_b"] = np.nanmean(gradient_pd[gate]["pos_b"])
            gradient_pd[gate]["qpc"] = np.nanmean(gradient_pd[gate]["qpc"])
        gradient_pd = gradient_pd.sort_index(0)
        gradient_pd = gradient_pd.sort_index(1)
        gradient_std_pd = gradient_std_pd.sort_index(0)
        gradient_std_pd = gradient_std_pd.sort_index(1)
        measurement_std_pd = measurement_std_pd.sort_index()
        return gradient_pd, gradient_std_pd, measurement_std_pd

    def initialize_prediction_kalman(self, gradient=None, covariance=None, noise=None, alpha=1.02):
        if gradient is None:
            gradient = self.calculate_prediction_gradient()[0].as_matrix()
        self.grad_kalman_prediction = GradKalmanFilter(nGates=4, nParams=3, initX=gradient, initP=covariance,
                                                       initR=noise, alpha=alpha)

    def prediction_center_diagram(self, d_voltages: pd.Series):
        for key in d_voltages.index:
            if key not in self.tunable_gates.index:
                d_voltages = d_voltages.drop(key)
        d_voltages = d_voltages.sort_index()
        d_voltages_vector = np.asarray(d_voltages)
        d_parameter_vector = np.dot(self.grad_kalman_prediction.grad, d_voltages_vector.transpose())
        current_qpc_position = self.dqd.read_qpc_voltage()["qpc"][0]
        qpc_shift = d_parameter_vector[2]
        predicted_qpc_position = current_qpc_position + qpc_shift
        tuning_output, actual_qpc_voltage = self.dqd.tune_qpc(qpc_position=float(predicted_qpc_position))
        actual_qpc_shift = float(actual_qpc_voltage["qpc"][0]) - current_qpc_position
        neg_position_shift = np.asarray([-1. * d_parameter_vector[0], -1. * d_parameter_vector[1]])
        correction = np.linalg.solve(self.grad_kalman.grad, neg_position_shift)
        self.track_qpc_while_shifting(d_voltages=correction)
        # correction_pd = pd.Series(data=correction, index=["BA", "BB"])
        # new_voltages = self.dqd.read_gate_voltages().add(correction_pd, fill_value=0)
        # self.dqd.set_gate_voltages(new_voltages)
        actual_position = np.asarray(self.measure_positions())
        total_position_shift = actual_position - self.central_position - neg_position_shift
        total_shift = [total_position_shift[0], total_position_shift[1], actual_qpc_shift]
        self.grad_kalman_prediction.update(dU=d_voltages_vector, dT=total_shift)

        self.center_diagram(remeasure_positions=False)

    def track_qpc_while_shifting(self, d_voltages):
        current_voltages = self.dqd.read_gate_voltages()
        d_voltages_norm = np.linalg.norm(d_voltages)
        n_steps = int(np.ceil(d_voltages_norm / 2.5e-3))
        for i in range(n_steps):
            voltage_step = d_voltages / float(n_steps) * (1. + float(i))
            voltage_step_pd = pd.Series(data=voltage_step, index=["BA", "BB"])
            new_voltages = current_voltages.add(voltage_step_pd, fill_value=0)
            self.dqd.set_gate_voltages(new_voltages)
            self.dqd.tune_qpc()

