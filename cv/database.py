import os
import sqlite3

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
VIDEO_DIR = os.path.join(DATA_DIR, 'recordings')


class Database:
    def __init__(self):
        self._db_file = os.path.join(DATA_DIR, 'person_detections.db')
        self._conn = None
        self._c = None
        self._table_name = 'person_detections'

        self._PATH_COL = 'path'
        self._DATE_COL = 'date'
        self._TIME_COL = 'time'
        self._NUM_PEOPLE_COL = 'num_people'
        self._columns = [
                (self._PATH_COL, 'TEXT'),
                (self._DATE_COL, 'TEXT'),
                (self._TIME_COL, 'TEXT'),
                (self._NUM_PEOPLE_COL, 'INTEGER')]

    def connect(self):
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
        if not os.path.isfile(self._db_file):
            init_required = True
        else:
            init_required = False

        self._conn = sqlite3.connect(self._db_file)
        self._c = self._conn.cursor()

        if init_required:
            create_table_cmd = 'CREATE TABLE {} ('.format(self._table_name)
            for c in self._columns:
                create_table_cmd += '{} {}'.format(c[0], c[1])
                if c != self._columns[-1]:
                    create_table_cmd += ','
            create_table_cmd += ')'
            self._c.execute(create_table_cmd)
            self._conn.commit()

        return self

    def add_entry(self, video_path, date, time, num_people):
        try:
            self._c.execute(
                    "INSERT INTO {} VALUES ('{}', '{}', '{}', '{}')".format(
                        self._table_name, video_path, date, time, num_people))
            self._conn.commit()
        except sqlite3.IntegrityError:
            print(
                    'ERROR: ID already exists in column {}'.format(
                        self._columns[0]))

    def _format_result(self, result):
        entry = {}
        entry['id'] = result[0]
        entry['path'] = result[1]
        entry['date'] = result[2]
        entry['time'] = result[3]
        entry['num_people'] = result[4]
        return entry

    def get_all(self, organize_by_date=False):
        try:
            self._c.execute('SELECT rowid, * FROM {}'.format(self._table_name))
            results = self._c.fetchall()
        except sqlite3.OperationalError:
            return []

        if organize_by_date:
            all_results = {}
            for result in results:
                entry = self._format_result(result)
                if all_results.get(entry['date'], None) is None:
                    all_results[entry['date']] = [entry]
                else:
                    all_results[entry['date']].append(entry)

            # Order lists for each date by the time of the recording
            for date in all_results:
                all_results[date] = sorted(all_results[date], key = lambda i: i['time'])

            return all_results

        else:
            all_results = []
            for result in results:
                entry = self._format_result(result)
                all_results.append(entry)
            return all_results

    def get_for_date(self, date):
        try:
            self._c.execute('SELECT rowid, * FROM {} WHERE {}={}'.format(
                self._table_name, self._DATE_COL, date))
            results = self._c.fetchall()
        except sqlite3.OperationalError:
            return []

        all_results = []
        for result in results:
            entry = self._format_result(result)
            all_results.append(entry)

        all_results = sorted(all_results, key = lambda i: i['time'])
        return all_results

    def get_for_id(self, id):
        try:
            self._c.execute('SELECT rowid, * FROM {} WHERE rowid={}'.format(
                self._table_name, id))
            results = self._c.fetchall()
        except sqlite3.OperationalError:
            print('Rowid not found!')
            return []

        entry = self._format_result(results[0])
        return entry

    def delete_by_id(self, id):
        try:
            self._c.execute('DELETE FROM {} WHERE rowid={}'.format(
                self._table_name, id))
            self._conn.commit()
        except sqlite3.OperationalError:
            print('Rowid not found!')

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._c = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, type, value, traceback):
        self.close()
