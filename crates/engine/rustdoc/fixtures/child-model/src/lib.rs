pub struct Builder<T>(pub T);

pub trait Bridge<T> {
    fn bridge(value: T) -> Self;
}

pub trait Model: Sized
where
    Self: Bridge<generated::Storage>,
    generated::Storage: Bridge<Self>,
{
    type Key;
    type Entity;
    type Column;

    fn query() -> Builder<Self> {
        loop {}
    }
}

#[model_macros::make_model]
pub struct Post {
    pub id: u64,
}

impl Post {
    pub fn source_method(&self) -> u64 {
        self.id
    }
}

pub use generated::{Column, Entity};

pub mod other {
    pub struct Post;
}
