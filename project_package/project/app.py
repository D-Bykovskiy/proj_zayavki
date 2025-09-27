"""Веб-интерфейс OMIS на Flask."""
from __future__ import annotations

import logging
from typing import Dict, List

from flask import Flask, flash, redirect, render_template, request, url_for
import sqlite3

from . import database

LOGGER = logging.getLogger(__name__)

def create_app() -> Flask:
    """Создать и настроить экземпляр Flask-приложения."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "change-me"  # TODO: вынести в config.py/.env

    logging.basicConfig(level=logging.INFO)

    database.init_db()

    @app.route("/")
    def index() -> str:
        """Переадресовать на список заявок."""
        return redirect(url_for("list_requests"))

    @app.route("/requests")
    def list_requests() -> str:
        """Отобразить список заявок из базы данных."""
        try:
            requests_data: List[Dict[str, str]] = database.get_requests()
        except sqlite3.Error:
            flash("Не удалось получить список заявок", "error")
            requests_data = []
        return render_template("requests.html", requests=requests_data)

    @app.route("/new")
    def new_request() -> str:
        """Показать форму создания заявки."""
        return render_template("new_request.html")

    @app.route("/add_request", methods=["POST"])
    def add_request() -> str:
        """Обработать отправку формы создания заявки."""
        form = request.form
        request_number = form.get("request_number", "").strip()
        position_number = form.get("position_number", "").strip()
        comment = form.get("comment", "").strip()

        if not request_number or not position_number:
            flash("Номер заявки и номер позиции обязательны", "error")
            return redirect(url_for("new_request"))

        try:
            database.add_request(request_number, position_number, comment)
            flash("Заявка успешно создана", "success")
        except sqlite3.IntegrityError:
            flash("Такая заявка уже существует", "error")
        except sqlite3.Error:
            LOGGER.exception("Ошибка при добавлении заявки")
            flash("Не удалось сохранить заявку", "error")
        return redirect(url_for("list_requests"))

    return app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)