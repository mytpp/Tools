#include "comment.h"

Comment::Comment(const QString& author,
                 const QString& content,
                 const QDate birthday)
    :authorId(author)
    ,content(content)
    ,birthday(birthday)
{

}
