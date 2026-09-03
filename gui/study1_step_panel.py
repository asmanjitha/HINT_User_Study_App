"""Shared pre-study training/familiarization phase."""
from __future__ import annotations

import datetime
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.application_controller import ApplicationController
from core.workflow_manager import (
    STEP_LABELS, TRAINING_CONDITIONS, STUDY1_TRAINING_REQUIRED_CONDITION_COUNT,
    condition_status_is_complete,
)
from devices.voice_recognizer import VoiceCommandRecognizer
from models.enums import Environment, Modality, WorkflowStep
from models.trial import ExperimentCondition

_STATUS = {"Not Started":"⬜","In Progress":"🔶","Completed":"✅","Manually Completed":"✅","Needs Repeat":"⚠"}

def _fmt(ts):
    return "--" if not ts else datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")

class Study1StepPanel(QWidget):
    def __init__(self, controller: ApplicationController, step: WorkflowStep, on_step_changed, parent=None):
        super().__init__(parent)
        if step != WorkflowStep.STUDY1_TRAINING:
            raise ValueError("Study1StepPanel is the shared Training Phase panel")
        self._controller=controller; self._step=step; self._on_step_changed=on_step_changed
        self._participant_code=None; self._active_run=None; self._active_live=False; self._active_remote=False
        root=QVBoxLayout(self)
        title=QLabel(STEP_LABELS[step]); title.setStyleSheet("font-size:18px;font-weight:bold;"); root.addWidget(title)
        note=QLabel(
            "Required practice covers Gridworld and Continuous Action Space. Anytime feedback practice is Keyboard-only. "
            "Joystick and Voice are practiced only in System-requested mode. HoloLens familiarization is optional and is listed last; it never blocks the studies."
        ); note.setWordWrap(True); note.setStyleSheet("color:#666;font-size:11px;"); root.addWidget(note)
        root.addWidget(self._build_table()); root.addWidget(self._build_config()); root.addWidget(self._build_run()); root.addWidget(self._build_history(),1)
        controller.rl_manager.trial_started.connect(lambda _t:self._set_live(True))
        controller.continuous_nav_client.connection_status_changed.connect(self._worker_status_changed)

    def _build_table(self):
        box=QGroupBox("Training checklist"); lay=QVBoxLayout(box)
        top=QHBoxLayout(); self._summary=QLabel(); self._summary.setStyleSheet("font-weight:bold;"); top.addWidget(self._summary); top.addStretch()
        self._mark_item=QPushButton("Mark Selected Item Complete…"); self._mark_item.clicked.connect(self._mark_selected_complete); top.addWidget(self._mark_item)
        self._quick=QPushButton("Quick Pass Required Training"); self._quick.clicked.connect(self._quick_pass); top.addWidget(self._quick)
        self._next=QPushButton("Select Next Required"); self._next.clicked.connect(self._select_next); top.addWidget(self._next); lay.addLayout(top)
        self._table=QTableWidget(len(TRAINING_CONDITIONS),6); self._table.setHorizontalHeaderLabels(["Practice item","Environment","Timing","Input","Required?","Status"])
        self._table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for c in range(1,6): self._table.horizontalHeader().setSectionResizeMode(c,QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.cellClicked.connect(self._select_row); lay.addWidget(self._table)
        return box

    def _build_config(self):
        box=QGroupBox("Selected training item"); form=QFormLayout(box)
        self._condition=QComboBox()
        for c in TRAINING_CONDITIONS: self._condition.addItem(c.label,c.key)
        self._condition.currentIndexChanged.connect(self._selection_changed); form.addRow("Training item:",self._condition)
        self._details=QLabel(); self._details.setWordWrap(True); form.addRow("Protocol:",self._details)
        self._seed=QSpinBox(); self._seed.setRange(0,2147483647); self._seed.setValue(int(self._controller.config.study_raw.get("random_seed",42))); form.addRow("Gridworld seed:",self._seed)
        self._warm=QCheckBox("Use maze-informed Actor/Critic warm start"); form.addRow("Gridworld warm start:",self._warm)
        room_cfg=self._controller.config.study_raw.get("continuous_room_navigation",{})
        row=QWidget(); h=QHBoxLayout(row); h.setContentsMargins(0,0,0,0)
        self._host=QLineEdit(str(room_cfg.get("worker_host","127.0.0.1"))); h.addWidget(self._host,1)
        self._port=QSpinBox(); self._port.setRange(1,65535); self._port.setValue(int(room_cfg.get("worker_port",8875))); h.addWidget(self._port)
        self._connect=QPushButton("Connect / Test"); self._connect.clicked.connect(self._connect_worker); h.addWidget(self._connect); form.addRow("Ubuntu worker:",row)
        self._worker_status=QLabel("Not connected"); self._worker_status.setWordWrap(True); form.addRow("Worker status:",self._worker_status)
        self._hil=QSpinBox(); self._hil.setRange(1,100); self._hil.setValue(int(room_cfg.get("hil_correction_length",10))); form.addRow("Correction length:",self._hil)
        self._timeout=QSpinBox(); self._timeout.setRange(1,120); self._timeout.setValue(int(room_cfg.get("feedback_timeout_seconds",10))); self._timeout.setSuffix(" s"); form.addRow("Feedback timeout:",self._timeout)
        return box

    def _build_run(self):
        box=QGroupBox("Training run"); lay=QVBoxLayout(box); self._status=QLabel("No run in progress."); lay.addWidget(self._status)
        self._folder=QLabel("Next data folder: --"); self._folder.setWordWrap(True); self._folder.setStyleSheet("font-family:monospace;color:#555;"); lay.addWidget(self._folder)
        row=QHBoxLayout(); self._start=QPushButton("Start Selected Practice"); self._start.clicked.connect(self._start_run); row.addWidget(self._start)
        self._complete=QPushButton("Stop && Mark Complete"); self._complete.clicked.connect(lambda:self._finish(True)); row.addWidget(self._complete)
        self._abort=QPushButton("Abort"); self._abort.clicked.connect(lambda:self._finish(False)); row.addWidget(self._abort); lay.addLayout(row); self._set_running(False); return box

    def _build_history(self):
        box=QGroupBox("Practice Trial History"); lay=QVBoxLayout(box); self._history=QTableWidget(0,7); self._history.setHorizontalHeaderLabels(["Condition","Attempt","Environment","Timing","Input","Result","Started"]); self._history.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch); self._history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); lay.addWidget(self._history); return box

    def set_participant(self, code): self._participant_code=code; self.refresh(); self._select_next()
    def refresh(self):
        self._refresh_table(); self._refresh_history(); self._selection_changed()
        if not self._participant_code:
            self._status.setText("Select or register a participant first."); self._start.setEnabled(False); self._set_running(False); return
        summary=self._controller.workflow_manager.step_status(self._participant_code,self._step); self._active_run=summary.active_run
        blocking=self._controller.workflow_manager.has_active_run(self._participant_code)
        if self._active_run:
            self._status.setText(f"Training in progress: {self._active_run.run_id}"); self._start.setEnabled(False); self._set_running(True)
        else:
            self._set_running(False)
            self._status.setText(f"Required training: {summary.completed_count}/{STUDY1_TRAINING_REQUIRED_CONDITION_COUNT} complete. Optional HoloLens familiarization does not affect this count.")
            self._start.setEnabled(blocking is None)
            if blocking: self._status.setText(f"Another run ({blocking.run_id}) is active. Finish or abort it first.")

    def _refresh_table(self):
        statuses=[] if not self._participant_code else self._controller.workflow_manager.training_condition_statuses(self._participant_code)
        completed=0
        for r,c in enumerate(TRAINING_CONDITIONS):
            st=statuses[r] if statuses else None
            if st and st.required and condition_status_is_complete(st.status): completed+=1
            vals=[c.label,c.environment.value,c.feedback_timing.value,c.modality.value,"Yes" if c.required else "Optional",f"{_STATUS[st.status]} {st.status}" if st else "⬜ Not Started"]
            for col,v in enumerate(vals): self._table.setItem(r,col,QTableWidgetItem(v))
        self._summary.setText(f"{completed} / {STUDY1_TRAINING_REQUIRED_CONDITION_COUNT} required training items completed")
        self._mark_item.setEnabled(bool(self._participant_code) and self._active_run is None)
        self._quick.setEnabled(bool(self._participant_code) and completed<STUDY1_TRAINING_REQUIRED_CONDITION_COUNT and self._active_run is None)
        self._next.setEnabled(bool(self._participant_code) and completed<STUDY1_TRAINING_REQUIRED_CONDITION_COUNT and self._active_run is None)

    def _refresh_history(self):
        if not self._participant_code: self._history.setRowCount(0); return
        trials=self._controller.trial_manager.list_trials(self._participant_code,practice=True); self._history.setRowCount(len(trials))
        for r,t in enumerate(reversed(trials)):
            vals=[t.condition_code or "--",t.run_code or "--",t.condition.environment.value,t.condition.feedback_timing.value,t.condition.modality.value,t.collection_status.value if t.collection_status.value!="Pending" else t.status.value,_fmt(t.started_at)]
            for c,v in enumerate(vals): self._history.setItem(r,c,QTableWidgetItem(v))

    def _selected(self):
        key=self._condition.currentData(); return next(c for c in TRAINING_CONDITIONS if c.key==key)
    def _select_row(self,row,_col):
        if 0<=row<len(TRAINING_CONDITIONS):
            idx=self._condition.findData(TRAINING_CONDITIONS[row].key); self._condition.setCurrentIndex(idx)
    def _select_next(self):
        if not self._participant_code:return
        x=self._controller.workflow_manager.next_incomplete_training_condition(self._participant_code)
        if x:
            idx=self._condition.findData(x.key)
            if idx>=0:self._condition.setCurrentIndex(idx)
    def _selection_changed(self):
        c=self._selected(); room=c.environment==Environment.CONTINUOUS_ROOM; grid=c.environment==Environment.GRIDWORLD
        self._details.setText(f"{c.environment.value} | {c.feedback_timing.value} | {c.modality.value} | {'Required' if c.required else 'Optional'}")
        self._seed.setEnabled(grid); self._warm.setEnabled(grid)
        for w in (self._host,self._port,self._connect,self._hil,self._timeout): w.setEnabled(room and self._active_run is None)
        self._update_preview()

    def _update_preview(self):
        if not self._participant_code: self._folder.setText("Next data folder: --"); return
        try:
            c=self._selected(); cond=ExperimentCondition(c.study,c.environment,c.feedback_timing,c.modality,rl_algorithm="ubuntu_ga3c_continuous_room" if c.environment==Environment.CONTINUOUS_ROOM else "actor_critic_gridworld",random_seed=self._seed.value() if c.environment==Environment.GRIDWORLD else None)
            sid=self._controller.session_manager.preview_session_id(self._participant_code); p=self._controller.trial_manager.preview_storage(participant_code=self._participant_code,session_id=sid,condition=cond,practice=True); self._folder.setText(f"Next data folder: {p['relative_dir']}")
        except Exception:self._folder.setText("Next data folder: unavailable")

    def _connect_worker(self):
        try:
            r=self._controller.connect_continuous_nav_worker(self._host.text().strip(),self._port.value()); self._worker_status.setText(f"Connected: {r.get('status',{}).get('hostname',self._host.text().strip())}")
        except Exception as e: self._worker_status.setText(f"Connection failed: {e}"); QMessageBox.critical(self,"Ubuntu worker connection failed",str(e))
    def _worker_status_changed(self,s): self._worker_status.setText(s)

    def _quick_pass(self):
        if not self._participant_code:return
        ans=QMessageBox.question(self,"Quick Pass Required Training",f"Mark all {STUDY1_TRAINING_REQUIRED_CONDITION_COUNT} required training items as passed? Optional HoloLens familiarization remains optional and is not synthesized.")
        if ans!=QMessageBox.StandardButton.Yes:return
        try:self._controller.workflow_manager.quick_pass_study1_training(self._participant_code)
        except Exception as e:QMessageBox.warning(self,"Could not Quick Pass",str(e));return
        self.refresh(); self._on_step_changed and self._on_step_changed()

    def _mark_selected_complete(self):
        if not self._participant_code or self._active_run is not None:return
        item=self._selected()
        status=self._controller.workflow_manager.training_condition_status(self._participant_code,item.key)
        if condition_status_is_complete(status.status):QMessageBox.information(self,"Already complete",f"'{item.label}' is already complete.");return
        reason,ok=QInputDialog.getText(self,"Manual completion reason",f"Reason for marking '{item.label}' complete:")
        if not ok:return
        try:self._controller.workflow_manager.mark_completion_override(self._participant_code,self._step,item_key=item.key,reason=reason)
        except Exception as e:QMessageBox.warning(self,"Could not mark item complete",str(e));return
        self.refresh(); self._select_next(); self._on_step_changed and self._on_step_changed()

    def _start_run(self):
        if not self._participant_code:return
        c=self._selected()
        if c.modality==Modality.VOICE:
            ok,msg=self._controller.device_manager.check_microphone()
            if not ok or not VoiceCommandRecognizer.backend_available(): QMessageBox.warning(self,"Voice unavailable",msg if not ok else "Vosk is unavailable."); return
        if c.modality==Modality.JOYSTICK:
            ok,msg=self._controller.device_manager.check_joystick()
            if not ok: QMessageBox.warning(self,"Joystick unavailable",msg); return
        if c.key=="hololens_optional":
            ok,msg=self._controller.device_manager.check_hololens()
            if not ok: QMessageBox.warning(self,"HoloLens unavailable",msg); return
        elif not self._controller.device_manager.beam_stream_healthy():
            answer=QMessageBox.question(self,"Beam is not receiving live gaze","No fresh Beam eye-tracking samples are reaching HINT. This training run will have no Beam gaze CSV or screen_gaze.mp4 recording.\n\nReturn to Devices, calibrate/connect Beam, and validate live gaze.\n\nStart this training activity without Beam recording anyway?")
            if answer!=QMessageBox.StandardButton.Yes:return
        if c.environment==Environment.CONTINUOUS_ROOM and not self._controller.continuous_nav_client.connected:
            try:self._controller.connect_continuous_nav_worker(self._host.text().strip(),self._port.value())
            except Exception as e:QMessageBox.critical(self,"Continuous worker unavailable",str(e));return
        try:
            run,session=self._controller.workflow_manager.start_run(self._participant_code,self._step)
            cond=ExperimentCondition(c.study,c.environment,c.feedback_timing,c.modality,rl_algorithm="ubuntu_ga3c_continuous_room" if c.environment==Environment.CONTINUOUS_ROOM else "actor_critic_gridworld",random_seed=self._seed.value() if c.environment==Environment.GRIDWORLD else None)
            if c.environment==Environment.CONTINUOUS_ROOM:
                trial=self._controller.start_continuous_room_trial(session.session_id,cond,practice=True,hil_correction_length=self._hil.value(),feedback_timeout_seconds=self._timeout.value()); self._active_remote=True; self._active_live=False
            else:
                trial=self._controller.start_actor_critic_trial(session.session_id,cond,practice=True,use_maze_qinit=self._warm.isChecked()); self._active_live=True; self._active_remote=False
            self._controller.workflow_manager.attach_trial(run.run_id,trial.trial_id); self._active_run=self._controller.workflow_manager.get_run(run.run_id); self.refresh()
        except Exception as e:
            if 'run' in locals(): self._controller.workflow_manager.end_run(run.run_id,completed=False,notes=f"Failed to start: {e}")
            QMessageBox.critical(self,"Could not start training",str(e)); self.refresh()

    def _finish(self,completed):
        if not self._active_run:return
        if self._controller.active_trial:self._controller.stop_active_trial(completed=completed)
        self._controller.workflow_manager.end_run(self._active_run.run_id,completed=completed)
        self._active_run=None; self._active_live=False; self._active_remote=False; self.refresh(); self._select_next(); self._on_step_changed and self._on_step_changed()
    def _set_running(self,r): self._complete.setEnabled(r); self._abort.setEnabled(r); self._condition.setEnabled(not r)
    def _set_live(self,_): pass
