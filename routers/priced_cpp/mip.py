"""Warm-started MILP helpers shared by optional optimization stages."""
import highspy
import numpy as np
from scipy.optimize import OptimizeResult
from scipy.sparse import csc_matrix


def solve_mip(cost, matrix, lower, upper, initial, bounds, seconds, integrality=None, mip_gap=1e-4, offset=0):
    matrix = matrix.tocsc()
    initial = np.asarray(initial, dtype=float)
    values = matrix @ initial
    if (np.any(values < np.asarray(lower)-1e-6) or
            np.any(values > np.asarray(upper)+1e-6) or
            np.any(initial < 0) or np.any(initial > np.asarray(bounds))):
        raise ValueError("incumbent violates MILP constraints")
    lp = highspy.HighsLp()
    lp.num_col_, lp.num_row_ = len(cost), len(lower)
    lp.offset_ = offset
    lp.col_cost_, lp.col_lower_, lp.col_upper_ = cost, np.zeros(len(cost)), bounds
    lp.row_lower_, lp.row_upper_ = lower, upper
    lp.integrality_ = [highspy.HighsVarType.kInteger if v else highspy.HighsVarType.kContinuous
                       for v in (integrality if integrality is not None else [1]*len(cost))]
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = matrix.indptr, matrix.indices, matrix.data
    solver = highspy.Highs()
    for name, value in (("output_flag", False), ("threads", 1),
                        ("time_limit", seconds), ("mip_rel_gap", mip_gap)):
        solver.setOptionValue(name, value)
    solver.passModel(lp)
    solver.setSolution(len(initial), np.arange(len(initial), dtype=np.int32), initial)
    solver.run()
    solution, info, status = solver.getSolution(), solver.getInfo(), solver.getModelStatus()
    feasible = solution.value_valid and info.primal_solution_status == highspy.SolutionStatus.kSolutionStatusFeasible
    dual_bound = getattr(info,"mip_dual_bound",float("nan"))
    return OptimizeResult(x=np.asarray(solution.col_value) if feasible else None,
                          fun=info.objective_function_value if feasible else None,
                          status=int(status), message=solver.modelStatusToString(status),
                          mip_gap=float(info.mip_gap) if feasible and np.isfinite(info.mip_gap) else None,
                          dual_bound=float(dual_bound) if np.isfinite(dual_bound) else None,
                          nodes=int(getattr(info,"mip_node_count",0)))


class LinearModel:
    def __init__(self):
        self.cost, self.rows, self.cols, self.values = [], [], [], []
        self.lower, self.upper, self.initial = [], [], []
        self.bounds, self.integrality = [], []
        self.offset = 0

    def variable(self, cost=0, initial=0, upper=1, integer=True):
        self.cost.append(cost)
        self.initial.append(initial)
        self.bounds.append(upper)
        self.integrality.append(int(integer))
        return len(self.cost)-1

    def constraint(self, terms, lower=-np.inf, upper=np.inf):
        row = len(self.lower)
        for variable,value in terms:
            self.rows.append(row); self.cols.append(variable); self.values.append(value)
        self.lower.append(lower); self.upper.append(upper)

    def solve(self, seconds, mip_gap=1e-4):
        matrix = csc_matrix((self.values,(self.rows,self.cols)),
                            shape=(len(self.lower),len(self.cost)),dtype=float)
        return solve_mip(self.cost,matrix,self.lower,self.upper,self.initial,self.bounds,
                         seconds,self.integrality,mip_gap,self.offset)
