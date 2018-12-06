#ifndef FORUMSTORAGE_H
#define FORUMSTORAGE_H


#include <QString>
#include <QVector>

class ForumStorage
{
public:
    ForumStorage();
    virtual ~ForumStorage();

    static ForumStorage* CreateStorage(const QString& category);

    virtual ForumStorage& operator <<(QVector<QString>& record) =0;
    virtual ForumStorage& operator >>(QVector<QString>& record) =0;
};

#endif // FORUMSTORAGE_H
