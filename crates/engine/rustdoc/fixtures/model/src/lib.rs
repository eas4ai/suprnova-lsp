pub struct Builder<T> {
    pub value: T,
}

pub trait Model: Sized {
    type Key;

    fn query() -> Builder<Self>;
}

#[fixture_macros::model]
pub struct Post {
    pub id: u64,
}

impl Post {
    pub fn source_method(&self) -> u64 {
        self.id
    }
}

pub mod other {
    pub struct Post;
}

pub trait BorrowedModel {}
impl BorrowedModel for &Post {}
impl BorrowedModel for Box<Post> {}

// Rust permits a record type and a function to share a name across namespaces.
#[allow(non_snake_case)]
pub fn Post() {}

pub enum Payload {
    Empty,
    One(Post),
    Named { value: Builder<Post> },
}

pub union Id {
    pub number: u64,
}

pub mod scoped {
    pub mod inner {
        pub struct Packet {
            pub(super) payload: crate::Builder<crate::Post>,
        }
    }
}

impl Post {
    pub fn bounded<T>(&self, value: T) -> T
    where
        T: IntoIterator<Item = Builder<Self>>,
    {
        value
    }
}
