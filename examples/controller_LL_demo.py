from constellation.all import *

log = plf.LogPile()

log.set_terminal_level(15)
log.log_levels[1].label_color = Fore.WHITE
# log.str_format.show_color_help = True

dmm = SiglentSDM3000X("TCPIP0::192.168.1.30::INSTR", log)
check_online(dmm, "DMM", log)

psu = RigolDP832("TCPIP0::192.168.1.238::INSTR", log)
check_online(psu, "PSU", log)

# Congiure DMM-i
dmm.set_measurement(DigitalMultimeter.MEAS_CURR_DC, 60e-3)
dmm.set_trigger_type(DigitalMultimeter.TRIG_SINGLE)

# ctrl = LocallyLinearSetpointController(
# 	set_x=lambda x: psu.set_voltage(3, x),
# 	get_measurement_y=lambda: dmm.send_trigger_and_read(),
# 	settle_s=2,
# 	tolerance_y=0.1e-3,
# 	x_min=0,
# 	x_max=0.8,
# 	dx_probe=0.05,
# 	max_step_x=0.1,
# 	avg_n=3,
# 	log=lambda x, y=10: log.add_log(y, f"LLSPCtrl message: >@:LOCK{x}@:UNLOCK<"),
# )
# 
# ctrl.set_target_y(1e-3, initial_x=0.6)

# Create a nonlinear controller
controller = MonotonicNonlinearController(log, max_step=0.2, default_probe_dx=0.05, backup_slope_tests=[], tol=25e-6, set_x_func=lambda x: psu.set_voltage(3, x), meas_y_func=lambda:dmm.send_trigger_and_read(), max_iterations=20, min_x=0)

controller.set_setpoint(1e-3, init_x=0.1)