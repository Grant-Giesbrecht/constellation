from constellation.base import *
from constellation.instrument_control.oscilloscope.oscilloscope_ctg import *
from constellation.ui import *

from PyQt6.QtWidgets import QWidget, QGridLayout, QLabel, QTextEdit, QPushButton, QLineEdit, QGroupBox
from PyQt6.QtGui import QDoubleValidator

class ChannelWidget(QWidget):
	
	def __init__(self, main_window, driver:Driver, log:plf.LogPile, channel_num:int):
		super().__init__(main_window)
		
		self.main_window = main_window
		self.driver = driver
		self.log = log
		self.channel_num = channel_num
		
		self.main_layout = QGridLayout()
		
		self.chan_label = QLabel(f"Channel {channel_num}")
		
		self.enable_button = QPushButton("Enable", parent=self)
		self.enable_button.setCheckable(True)
		
		self.vdiv_label = QLabel("Volts/div [V]:")
		self.vdiv_edit = QLineEdit()
		self.vdiv_edit.setValidator(QDoubleValidator())
		vdiv_val = driver.state[BasicOscilloscopeCtg.DIV_VOLT].get_idx_val(self.channel_num)
		self.vdiv_edit.setText(f"{vdiv_val}")
		self.vdiv_edit.setFixedWidth(80)
		
		self.voff_label = QLabel("Voltage offset [V]:")
		self.voff_edit = QLineEdit()
		self.voff_edit.setValidator(QDoubleValidator())
		temp = driver.state[BasicOscilloscopeCtg.OFFSET_VOLT].get_idx_val(self.channel_num)
		self.voff_edit.setText(f"{temp}")
		self.voff_edit.setFixedWidth(80)
		
		self.main_layout.addWidget(self.chan_label, 0, 0, 1, 2)
		self.main_layout.addWidget(self.enable_button, 1, 0, 1, 2)
		self.main_layout.addWidget(self.vdiv_label, 2, 0)
		self.main_layout.addWidget(self.vdiv_edit, 2, 1)
		self.main_layout.addWidget(self.voff_label, 3, 0)
		self.main_layout.addWidget(self.voff_edit, 3, 1)
		
		# self.xmin_edit.editingFinished.connect(self.apply_changes)
		
		self.setLayout(self.main_layout)
	
	def state_to_ui(self):
		
		self.log.lowdebug(f"Channel {self.channel_num} updating UI from state.")
		
		self.enable_button.setChecked(self.driver.state[BasicOscilloscopeCtg.CHAN_EN].get_idx_val(self.channel_num))
		
		self.vdiv_edit.setText(str(self.driver.state[BasicOscilloscopeCtg.DIV_VOLT].get_idx_val(self.channel_num)))
		
		self.voff_edit.setText(str(self.driver.state[BasicOscilloscopeCtg.OFFSET_VOLT].get_idx_val(self.channel_num)))

class BasicOscilloscopeWidget(InstrumentWidget):
	
	def __init__(self, main_window, driver:Driver, log:plf.LogPile):
		super().__init__(main_window, driver, log)
		
		self.chan_widgets = {}
		
		self.channel_box = QGroupBox()
		self.channel_box_layout = QGridLayout()
		self.channel_box.setLayout(self.channel_box_layout)
		
		# Init all channels
		for i in range(self.driver.first_channel, self.driver.first_channel+self.driver.max_channels):
			self.chan_widgets[i] = ChannelWidget(self.main_window, self.driver, self.log, i)
			
			self.channel_box_layout.addWidget(self.chan_widgets[i], 1, i)
		
		self.plot_widget = PlotWidget(self.main_window, self.log)
		
		self.horiz_label = QLabel("Horizontal")
		
		self.tdiv_label = QLabel("Time/div [s]:")
		self.tdiv_edit = QLineEdit()
		self.tdiv_edit.setValidator(QDoubleValidator())
		tdiv_val = driver.state[BasicOscilloscopeCtg.DIV_TIME]
		self.tdiv_edit.setText(f"{tdiv_val}")
		self.tdiv_edit.setFixedWidth(80)
		
		self.toff_label = QLabel("Time offset [s]:")
		self.toff_edit = QLineEdit()
		self.toff_edit.setValidator(QDoubleValidator())
		temp = driver.state[BasicOscilloscopeCtg.OFFSET_TIME]
		self.toff_edit.setText(f"{temp}")
		self.toff_edit.setFixedWidth(80)
		
		self.horiz_box = QGroupBox()
		self.horiz_box_layout = QGridLayout()
		self.horiz_box_layout.addWidget(self.horiz_label, 0, 0, 1, 2)
		self.horiz_box_layout.addWidget(self.tdiv_label, 1, 0)
		self.horiz_box_layout.addWidget(self.tdiv_edit, 1, 1)
		self.horiz_box_layout.addWidget(self.toff_label, 2, 0)
		self.horiz_box_layout.addWidget(self.toff_edit, 2, 1)
		self.horiz_box.setLayout(self.horiz_box_layout)
		
		self.main_layout.addWidget(self.plot_widget, 0, 0)
		self.main_layout.addWidget(self.horiz_box, 0, 1)
		self.main_layout.addWidget(self.channel_box, 1, 0, 1, 2)
		
		self.setLayout(self.main_layout)
	
	def state_to_ui(self):
		
		self.plot_widget.ax1a.cla()
		
		self.log.debug(f"Refreshing UI from driver state.")
		
		self.tdiv_edit.setText(str(self.driver.state[BasicOscilloscopeCtg.DIV_TIME]))
		self.toff_edit.setText(str(self.driver.state[BasicOscilloscopeCtg.OFFSET_TIME]))
		
		# Init all channels
		for i in range(self.driver.first_channel, self.driver.first_channel+self.driver.max_channels):
			
			self.chan_widgets[i].state_to_ui()
		
			wav = self.driver.get_waveform(i)
			
			self.plot_widget.ax1a.plot(wav['time_s'], wav['volt_V'])
		
		self.plot_widget.ax1a.grid(True)
		self.plot_widget.ax1a.grid("Time [S]")
		self.plot_widget.ax1a.set_ylabel("Voltage [V]")
		
		self.plot_widget.fig1.tight_layout()
		self.plot_widget.fig1.canvas.draw_idle()